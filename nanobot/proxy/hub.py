"""Proxy TCP server for hub <-> proxy communication.

Manages TCP connections with proxy processes. Each proxy maintains
a long-lived TCP connection for messages and responses. No HTTP,
no heartbeat — connection liveness is the heartbeat.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from typing import Any

from loguru import logger

from nanobot.proxy.manager import ProxyManager
from nanobot.proxy.protocol import HubResponse, ProxyMessage, outbound_to_hub_response
from nanobot.utils.tool_hints import format_single_tool_hint


class _PendingItem:
    """Queue item for mid-turn injection.

    Duck-types as InboundMessage for the agent loop's _drain_pending,
    and carries the original data dict + TCP writer handles for
    re-dispatch when unconsumed after the current turn completes.
    """
    __slots__ = ('inbound', 'data', 'write_lock', 'writer', 'peername')

    def __init__(self, inbound: Any, data: dict, write_lock: Any, writer: Any, peername: Any) -> None:
        self.inbound = inbound
        self.data = data
        self.write_lock = write_lock
        self.writer = writer
        self.peername = peername

    @property
    def content(self) -> str:
        return self.inbound.content

    @property
    def media(self) -> list[str]:
        return self.inbound.media

    @property
    def channel(self) -> str:
        return self.inbound.channel


class HubTCPServer:
    """TCP server that accepts proxy connections and routes messages to AgentLoop.

    Protocol:
    - Proxy sends JSON lines (ProxyMessage) over TCP
    - Hub responds with JSON lines (HubResponse) over same TCP
    - Connection close = proxy death signal
    """

    _DEDUP_TTL = 300  # seconds to keep a message_id for dedup
    MAX_SESSION_CACHE = 1000

    def _prune_session_cache(self) -> None:
        """Evict oldest session entries with empty queues when over limit."""
        if len(self._session_locks) <= self.MAX_SESSION_CACHE:
            return
        excess = len(self._session_locks) - self.MAX_SESSION_CACHE
        removed = 0
        for key in list(self._session_locks.keys()):
            if removed >= excess:
                break
            queue = self._session_pending_queues.get(key)
            if queue is None or queue.empty():
                del self._session_locks[key]
                self._session_pending_queues.pop(key, None)
                self._session_tasks.pop(key, None)
                removed += 1

    def __init__(
        self,
        host: str,
        port: int,
        agent_loop: Any,
        proxy_manager: ProxyManager,
        concurrency_gate: asyncio.Semaphore | None = None,
    ):
        self._host = host
        self._port = port
        self._agent_loop = agent_loop
        self._proxy_manager = proxy_manager
        self._concurrency_gate = concurrency_gate
        self._server: asyncio.Server | None = None
        # message_id → expiry timestamp; used to drop duplicate messages
        # from proxy reconnections before they reach the agent loop.
        self._seen_message_ids: dict[str, float] = {}
        # Per-session dispatch locks — serialize concurrent _route_message
        # tasks for the same session (e.g. when proxy reconnects and delivers
        # a new message while the previous turn is still processing).
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Per-session pending queues for mid-turn injection. When the session
        # lock is held, new messages are enqueued here instead of blocking,
        # and consumed between tool calls by the agent loop's _drain_pending.
        self._session_pending_queues: dict[str, asyncio.Queue] = {}
        # Per-session running task — tracked so /stop can cancel the current
        # processing instead of being queued for mid-turn injection.
        self._session_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Start the TCP server and begin accepting proxy connections.

        Retries on Windows WSAEADDRINUSE / WSAEACCES which occur when the
        previous instance's port is still in TIME_WAIT (typically 30-60s).
        SO_REUSEADDR alone is insufficient on Windows — the *original* socket
        must also have had it set, which asyncio.start_server's default socket
        does not.  Retrying with a delay is the reliable workaround.
        """
        import socket as _socket

        # Windows TIME_WAIT is 2 * MSL = ~60s; retries give a 75s window.
        max_attempts = 25
        retry_delay = 3.0  # seconds

        for attempt in range(1, max_attempts + 1):
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self._host, self._port))
                break  # bind succeeded
            except OSError as e:
                sock.close()
                # Windows TIME_WAIT errors: WSAEADDRINUSE (10048) or
                # WSAEACCES (10013) — the latter occurs when the original
                # socket didn't have SO_REUSEADDR set.
                win_err = getattr(e, "winerror", 0)
                if win_err in (10048, 10013) and attempt < max_attempts:
                    logger.warning(
                        "Port {}:{} busy (winerror={}), retrying in {:.0f}s "
                        "(attempt {}/{})",
                        self._host, self._port, win_err, retry_delay,
                        attempt, max_attempts,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                raise

        sock.listen(100)
        sock.setblocking(False)

        self._server = await asyncio.start_server(
            self._handle_client,
            sock=sock,
        )
        logger.info("Proxy TCP server listening on {}:{}", self._host, self._port)

    async def stop(self) -> None:
        """Stop the TCP server gracefully."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Proxy TCP server stopped")

    @staticmethod
    def _setup_keepalive(transport: asyncio.Transport) -> None:
        """Enable TCP keepalive with sensible defaults (cross-platform)."""
        sock = transport.get_extra_info("socket")
        if sock is None:
            return
        try:
            import platform as _platform
            if _platform.system() == "Windows":
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                for opt, val in [
                    (socket.TCP_KEEPIDLE, 30),
                    (socket.TCP_KEEPINTVL, 10),
                    (socket.TCP_KEEPCNT, 3),
                ]:
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, opt, val)
                    except AttributeError:
                        pass
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except AttributeError:
                pass
        except (OSError, AttributeError) as e:
            logger.debug("Could not set TCP keepalive: {}", e)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single proxy TCP connection.

        All writes to *writer* are serialized through a per-connection lock
        to prevent interleaving between the main loop (register response)
        and concurrently dispatched _route_message tasks.

        Connection liveness is detected via TCP keepalive and EOF.
        """
        peername = writer.get_extra_info("peername")
        logger.info("Proxy TCP connection from {}", peername)
        # Raise readline limit from default 64KB to 1MB to prevent truncated
        # reads on long messages (e.g. Feishu messages with large content).
        reader._limit = 1024 * 1024
        self._setup_keepalive(writer.transport)
        # Use the shared per-proxy write lock so deliver_to_proxy and
        # register/error writes are serialized through the same lock.
        proxy_key: str | None = None  # set on register
        proxy_write_lock: asyncio.Lock = asyncio.Lock()  # fallback before register
        # Track in-flight _route_message tasks so they can be cancelled when
        # the TCP connection drops — prevents zombie tasks from completing
        # on a dead connection and delivering responses into the void.
        _pending_tasks: set[asyncio.Task] = set()

        def _track_task(task: asyncio.Task) -> None:
            _pending_tasks.add(task)
            task.add_done_callback(_pending_tasks.discard)

        async def _write(data: dict) -> None:
            async with proxy_write_lock:
                writer.write((json.dumps(data) + "\n").encode())
                await writer.drain()

        try:
            while True:
                try:
                    line_bytes = await reader.readline()
                except ValueError:
                    logger.warning(
                        "Oversized message from proxy {} (exceeds 1MB limit), disconnecting",
                        peername,
                    )
                    break
                if not line_bytes:
                    break  # EOF — connection closed

                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from proxy {}: {}", peername, line[:100])
                    continue

                msg_type = data.get("type", "")

                if msg_type == "register":
                    channel = data.get("channel", "")
                    bot = data.get("bot", "")
                    pid = data.get("pid", 0)
                    key = f"{channel}:{bot}"
                    proxy_key = key

                    accepted = self._proxy_manager.register_via_tcp(
                        key, reader, writer,
                        {"channel": channel, "bot": bot, "pid": pid},
                    )
                    if not accepted:
                        logger.warning(
                            "Proxy registration rejected: {}:{} (pid={})",
                            channel, bot, pid,
                        )
                        break

                    logger.info("Proxy registered: {}:{} (pid={})", channel, bot, pid)
                    # Switch to the shared per-proxy write lock so all writes
                    # to this proxy go through the same serialization.
                    proxy_write_lock = self._proxy_manager.get_write_lock(proxy_key)
                    await _write(HubResponse(success=True).to_dict())

                elif msg_type == "message":
                    # Process asynchronously so other messages from the same
                    # proxy are still serviced during long LLM calls.
                    task = asyncio.create_task(self._route_message(proxy_write_lock, writer, data, peername))
                    _track_task(task)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Proxy TCP connection error from {}: {}", peername, e)
        finally:
            logger.info("Proxy TCP disconnected: {}", peername)
            self._proxy_manager.unregister_by_writer(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                logger.warning("Error closing proxy TCP writer", exc_info=True)
            # Cancel any in-flight route_message tasks — they're running on
            # a dead connection and would deliver responses into the void.
            for task in list(_pending_tasks):
                task.cancel()
            if _pending_tasks:
                logger.debug("Cancelled {} zombie tasks for {}", len(_pending_tasks), peername)
            # Clean up session locks for this proxy — prevents memory leak
            if proxy_key:
                prefix = f"{proxy_key}:"
                for k in list(self._session_locks.keys()):
                    if k.startswith(prefix):
                        del self._session_locks[k]
                for k in list(self._session_pending_queues.keys()):
                    if k.startswith(prefix):
                        del self._session_pending_queues[k]
                logger.debug("Cleaned up session state for proxy {}", proxy_key)

    async def _deliver_stop_response(
        self,
        proxy_key: str,
        msg: ProxyMessage,
        session_key: str,
        session_lock: asyncio.Lock,
    ) -> None:
        """Process /stop through LLM and deliver the response to the proxy."""
        try:
            async with session_lock:
                response = await self._agent_loop.process_direct(
                    content="/stop",
                    session_key=session_key,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    metadata={"_stop_redispatch": True},
                )
            if response and response.content:
                await self._proxy_manager.deliver_to_proxy(proxy_key, {
                    "type": "deliver",
                    "chat_id": msg.chat_id,
                    "content": response.content,
                })
        except Exception as e:
            logger.warning("Error processing /stop through LLM: {}", e)
            # Fallback: send simple confirmation
            await self._proxy_manager.deliver_to_proxy(proxy_key, {
                "type": "deliver",
                "chat_id": msg.chat_id,
                "content": "✅ 已停止",
            })

    async def _route_message(
        self,
        write_lock: asyncio.Lock,
        writer: asyncio.StreamWriter,
        data: dict[str, Any],
        peername: Any,
    ) -> None:
        """Deserialize a ProxyMessage, process it through the agent, and reply."""
        try:
            msg = ProxyMessage.from_dict(data)
        except Exception as e:
            logger.warning("Invalid ProxyMessage from proxy {}: {}", peername, e)
            async with write_lock:
                writer.write((json.dumps(HubResponse(success=False, error=str(e)).to_dict()) + "\n").encode())
                await writer.drain()
            return

        session_key = f"{msg.channel}:{msg.bot}:{msg.sender_id}"
        proxy_key = f"{msg.channel}:{msg.bot}"

        # ── Message dedup ────────────────────────────────────────────────
        # Proxy reconnections can re-deliver the same message_id over a new
        # TCP connection.  Drop duplicates here so they never reach the agent
        # loop and corrupt session state.
        # Key includes proxy_key so different sessions can't collide on
        # platform-agnostic message IDs.
        dedup_key = f"{proxy_key}:{msg.message_id}" if proxy_key else msg.message_id
        if msg.message_id:
            now = time.time()
            expiry = self._seen_message_ids.get(dedup_key, 0)
            if expiry > now:
                logger.debug("Dropped duplicate message {} from {}", msg.message_id, peername)
                return
            self._seen_message_ids[dedup_key] = now + self._DEDUP_TTL
            # Prune stale entries when the map grows large enough.
            # If expired-only prune doesn't free enough, remove oldest 25%.
            if len(self._seen_message_ids) > 10000:
                stale = [mid for mid, exp in self._seen_message_ids.items() if exp <= now]
                for mid in stale:
                    del self._seen_message_ids[mid]
                if len(self._seen_message_ids) > 10000:
                    sorted_keys = sorted(self._seen_message_ids.keys(), key=lambda k: self._seen_message_ids[k])
                    for k in sorted_keys[:len(sorted_keys) // 4]:
                        del self._seen_message_ids[k]
        logger.info(
            "TCP proxy message for {}: {} (session={})",
            session_key, msg.content[:50], session_key,
        )

        # Progress callback delivers /think and /tool observe events
        # to the proxy in real-time while the main request is processing.
        async def _on_progress(
            content: str,
            *,
            tool_hint: bool = False,
            tool_events: list | None = None,
        ) -> None:
            # User-facing tools must not leak any progress notifications into
            # the chat — their output IS the response.
            _user_tools = frozenset(["message_tool"])

            has_non_user_event = False
            if tool_events:
                for te in tool_events:
                    if not isinstance(te, dict):
                        continue
                    phase = te.get("phase", "")
                    name = te.get("name", "tool")
                    if name in _user_tools:
                        continue
                    has_non_user_event = True
                    args = te.get("arguments", {})
                    if isinstance(args, dict) and args:
                        hint = format_single_tool_hint(name, args)
                    else:
                        hint = name
                    if phase == "end":
                        text = f"✅ {hint} completed"
                    elif phase == "error":
                        error = te.get("error", "")
                        text = f"❌ {hint}: {error}" if error else f"❌ {hint} failed"
                    else:
                        continue
                    await self._proxy_manager.deliver_to_proxy(proxy_key, {
                        "type": "deliver",
                        "chat_id": msg.chat_id,
                        "content": text,
                    })
            # Send thinking text / tool hints only when not exclusively about
            # user-facing tools.  When ``tool_events`` is None/empty the
            # content is genuine thinking text and should always be shown.
            if content and (not tool_events or has_non_user_event):
                await self._proxy_manager.deliver_to_proxy(proxy_key, {
                    "type": "deliver",
                    "chat_id": msg.chat_id,
                    "content": content,
                })

        inbound = msg.to_inbound_message()

        # Serialize concurrent _route_message tasks for the same session.
        # Without this, a second message from the same user arriving during
        # LLM processing (e.g. after proxy reconnect) creates a race condition
        # where both tasks read/write session state concurrently.
        session_lock = self._session_locks.get(session_key)
        if session_lock is None:
            session_lock = asyncio.Lock()
            self._session_locks[session_key] = session_lock
            self._prune_session_cache()

        # If the lock is already held, the session is busy.  Enqueue for
        # mid-turn injection rather than blocking on the lock — except /stop
        # which cancels the running task immediately.
        if session_lock.locked():
            if msg.content.strip() == "/stop":
                # Lock held — the running task registered itself via
                # _session_tasks when it acquired the lock.  Read (not
                # overwrite) that reference so we cancel the right task.
                task = self._session_tasks.get(session_key)
                if task and not task.done():
                    task.cancel()
                    logger.info("Stopped session {} via /stop", session_key)
                    try:
                        await task  # wait for full unwind
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("Unexpected error awaiting cancelled session task")
                # Process /stop through LLM so it can update TREE.md
                # and confirm with the user over this proxy connection.
                asyncio.create_task(self._deliver_stop_response(
                    proxy_key, msg, session_key, session_lock,
                ))
                return
            logger.debug("Session {} busy, enqueuing for mid-turn injection", session_key)
            queue = self._session_pending_queues.setdefault(session_key, asyncio.Queue())
            queue.put_nowait(_PendingItem(inbound, data, write_lock, writer, peername))
            return

        # Lock is free — register this task so /stop can find it.
        self._session_tasks[session_key] = asyncio.current_task()

        # Use existing pending queue (setdefault is atomic,
        # preventing the race where two concurrent _route_message tasks each
        # create their own queue, and a third message's mid-turn injection
        # ends up in the wrong one).
        pending_queue = self._session_pending_queues.setdefault(session_key, asyncio.Queue())

        try:
            async with session_lock:
                if self._concurrency_gate:
                    async with self._concurrency_gate:
                        response = await self._agent_loop.process_direct(
                            content=inbound.content,
                            session_key=session_key,
                            channel=inbound.channel,
                            chat_id=inbound.chat_id,
                            media=inbound.media or None,
                            on_progress=_on_progress,
                            pending_queue=pending_queue,
                        )
                else:
                    response = await self._agent_loop.process_direct(
                        content=inbound.content,
                        session_key=session_key,
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        media=inbound.media or None,
                        metadata=inbound.metadata,
                        on_progress=_on_progress,
                        pending_queue=pending_queue,
                    )
            if response is None:
                return
            resp = outbound_to_hub_response(response, reply_to=msg.message_id)
        except asyncio.CancelledError:
            logger.info("Session {} processing cancelled", session_key)
            raise
        except Exception as e:
            logger.exception("Error processing proxy TCP message: {}", e)
            resp = HubResponse(success=False, error=str(e))
        finally:
            self._session_tasks.pop(session_key, None)

        # After dispatch, re-dispatch any messages that arrived too late
        # for _drain_pending to consume during the agent loop.
        remaining_items: list[_PendingItem] = []
        while not pending_queue.empty():
            try:
                remaining_items.append(pending_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if remaining_items:
            logger.info(
                "Re-dispatching {} messages for session {}",
                len(remaining_items), session_key,
            )
            for item in remaining_items:
                # Clear dedup entry so re-dispatch isn't dropped as duplicate
                pard = ProxyMessage.from_dict(item.data)
                if pard.message_id:
                    dk = f"{proxy_key}:{pard.message_id}" if proxy_key else pard.message_id
                    self._seen_message_ids.pop(dk, None)
                task = asyncio.create_task(self._route_message(
                    item.write_lock, item.writer, item.data, item.peername,
                ))
                # Re-dispatched tasks are already tracked by _handle_client
                # (_pending_tasks in the closure scope), skip re-tracking.

        # Route response through proxy_manager so it reaches the CURRENT
        # TCP connection — the proxy may have reconnected during processing.
        proxy_key = f"{msg.channel}:{msg.bot}"
        resp_dict = resp.to_dict()
        resp_dict["type"] = "deliver"
        resp_dict["chat_id"] = msg.chat_id
        delivered = await self._proxy_manager.deliver_to_proxy(
            proxy_key, resp_dict,
        )
        if delivered:
            logger.info(
                "Hub response delivered to proxy {}: content={}",
                proxy_key, resp_dict.get("content", "")[:60],
            )
        else:
            logger.warning(
                "Response not delivered to proxy {} (disconnected during processing)",
                proxy_key,
            )
