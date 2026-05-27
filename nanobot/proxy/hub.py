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

    def __init__(
        self,
        host: str,
        port: int,
        agent_loop: Any,
        proxy_manager: ProxyManager,
        bus: Any = None,
        concurrency_gate: asyncio.Semaphore | None = None,
    ):
        self._host = host
        self._port = port
        self._agent_loop = agent_loop
        self._proxy_manager = proxy_manager
        self._bus = bus
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

    async def start(self) -> None:
        """Start the TCP server and begin accepting proxy connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
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
                line_bytes = await reader.readline()
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
                    seq = data.get("_seq")
                    # Process asynchronously so other messages from the same
                    # proxy are still serviced during long LLM calls.
                    task = asyncio.create_task(self._route_message(proxy_write_lock, writer, data, peername, seq=seq))
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
                logger.warning("Error closing proxy TCP writer")
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

    async def _route_message(
        self,
        write_lock: asyncio.Lock,
        writer: asyncio.StreamWriter,
        data: dict[str, Any],
        peername: Any,
        seq: int | None = None,
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
        if msg.message_id:
            now = time.time()
            expiry = self._seen_message_ids.get(msg.message_id, 0)
            if expiry > now:
                logger.debug("Dropped duplicate message {} from {}", msg.message_id, peername)
                return
            self._seen_message_ids[msg.message_id] = now + self._DEDUP_TTL
            # Prune stale entries when the map grows large enough
            if len(self._seen_message_ids) > 10000:
                stale = [mid for mid, exp in self._seen_message_ids.items() if exp <= now]
                for mid in stale:
                    del self._seen_message_ids[mid]
        logger.info(
            "TCP proxy message for {}: {} (session={})",
            session_key, msg.content[:50], session_key,
        )

        # ── Outbound bridge ────────────────────────────────────────────
        # The message tool sends via bus.publish_outbound.  Consume these
        # during processing so they reach the proxy in real-time.
        _outbound_bridge_task: asyncio.Task | None = None

        async def _bridge_outbound() -> None:
            bus = self._bus
            try:
                while True:
                    try:
                        outbound = await asyncio.wait_for(
                            bus.consume_outbound(), timeout=1.0,
                        )
                    except asyncio.TimeoutError:
                        continue
                    # Only forward messages for this proxy's channel
                    if outbound.channel != msg.channel:
                        continue
                    payload: dict[str, Any] = {
                        "type": "deliver",
                        "chat_id": outbound.chat_id,
                        "content": outbound.content,
                    }
                    if outbound.media:
                        payload["media"] = outbound.media
                    if outbound.buttons:
                        payload["buttons"] = outbound.buttons
                    await self._proxy_manager.deliver_to_proxy(proxy_key, payload)
            except asyncio.CancelledError:
                pass

        # Progress callback delivers /think and /tool observe events
        # to the proxy in real-time while the main request is processing.
        async def _on_progress(
            content: str,
            *,
            tool_hint: bool = False,
            tool_events: list | None = None,
        ) -> None:
            # Send tool finish/error events — these arrive with empty content
            # from after_iteration but carry structured event data.
            if tool_events:
                for te in tool_events:
                    if not isinstance(te, dict):
                        continue
                    phase = te.get("phase", "")
                    name = te.get("name", "tool")
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
            # Send thinking text and tool hint text
            if content:
                await self._proxy_manager.deliver_to_proxy(proxy_key, {
                    "type": "deliver",
                    "chat_id": msg.chat_id,
                    "content": content,
                })

        inbound = msg.to_inbound_message()

        # Start outbound bridge before processing
        _outbound_bridge_task = asyncio.create_task(_bridge_outbound())

        # Serialize concurrent _route_message tasks for the same session.
        # Without this, a second message from the same user arriving during
        # LLM processing (e.g. after proxy reconnect) creates a race condition
        # where both tasks read/write session state concurrently.
        session_lock = self._session_locks.get(session_key)
        if session_lock is None:
            session_lock = asyncio.Lock()
            self._session_locks[session_key] = session_lock

        # If the lock is already held, the session is busy.  Enqueue for
        # mid-turn injection rather than blocking on the lock.
        if session_lock.locked():
            logger.debug("Session {} busy, enqueuing for mid-turn injection", session_key)
            queue = self._session_pending_queues.setdefault(session_key, asyncio.Queue())
            queue.put_nowait(_PendingItem(inbound, data, write_lock, writer, peername))
            return

        # Lock is free — create a fresh pending queue for this dispatch.
        pending_queue: asyncio.Queue = asyncio.Queue()
        self._session_pending_queues[session_key] = pending_queue

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
        except Exception as e:
            logger.exception("Error processing proxy TCP message: {}", e)
            resp = HubResponse(success=False, error=str(e))
        finally:
            if _outbound_bridge_task is not None and not _outbound_bridge_task.done():
                _outbound_bridge_task.cancel()
                try:
                    await _outbound_bridge_task
                except asyncio.CancelledError:
                    pass

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
                    self._seen_message_ids.pop(pard.message_id, None)
                asyncio.create_task(self._route_message(
                    item.write_lock, item.writer, item.data, item.peername,
                ))

        # Route response through proxy_manager so it reaches the CURRENT
        # TCP connection — the proxy may have reconnected during processing.
        proxy_key = f"{msg.channel}:{msg.bot}"
        resp_dict = resp.to_dict()
        resp_dict["type"] = "deliver"
        resp_dict["chat_id"] = msg.chat_id
        if seq is not None:
            resp_dict["_seq"] = seq  # echo back for waiter matching
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
