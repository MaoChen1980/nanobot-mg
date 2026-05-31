"""Base class for all proxy channels — handles TCP communication with Hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import socket
import sys
import threading
import time
from typing import Any

from loguru import logger

from nanobot.proxy.protocol import HubResponse


class BaseProxyChannel:
    """Common TCP connection and message-forwarding logic for proxy channels.

    Subclasses must define:
        CHANNEL_NAME: str          — human-readable name (e.g. "Feishu")
        REQUIRED_CONFIG_FIELDS: list[str] — config keys checked at startup
        start(self)                — enter the channel's own message loop
        send_reply(self, chat_id, reply_to, content) — send a text reply
    """

    CHANNEL_NAME = ""
    REQUIRED_CONFIG_FIELDS: list[str] = []

    def __init__(
        self,
        config: dict[str, Any],
        hub_tcp_host: str,
        hub_tcp_port: int,
        channel: str,
        bot: str,
    ):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._max_message_age = int(config.get("max_message_age", 300))
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._conn_loop: asyncio.AbstractEventLoop | None = None
        self._conn_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._parent_watch_task: asyncio.Task | None = None
        # seq-numbered response futures — each _send_raw call gets a unique
        # sequence number so _background_reader can route responses correctly
        # without a shared single-slot future.
        self._response_futures: dict[int, asyncio.Future] = {}
        self._next_seq: int = 0
        self._parent_pid: int = 0  # set after TCP connect
        # FIFO send queue — linearizes outbound messages so push deliveries
        # (tool/think events) always arrive before the reply.
        self._send_queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._send_worker_loop, daemon=True).start()
        # msg_id -> timestamp for deduplication
        self._dedup: dict[str, float] = {}
        self._last_chat_id: str = ""  # last chat_id to send startup notification to

    # ------------------------------------------------------------------
    # Lifecycle: startup notification
    # ------------------------------------------------------------------

    async def _send_startup_notification(self) -> None:
        """Send startup notification to the last chat that messaged us.

        Override in subclass to use non-blocking _send_plain_text.
        Base stub does nothing — avoids AttributeError if subclass doesn't have _send_plain_text.
        """
        pass  # base stub

    def notify_ready(self) -> None:
        """Called by subclasses when the channel is ready — sends startup message to last chat."""
        if self._last_chat_id and hasattr(self, "_send_plain_text"):
            self._send_plain_text(self._last_chat_id, "Nano Bot 已启动，Proxy ready")
            logger.info("Sent startup notification to {}", self._last_chat_id)

    # ------------------------------------------------------------------
    # TCP keepalive
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # TCP connection lifecycle
    # ------------------------------------------------------------------

    def connect_to_hub(self) -> None:
        """Connect to Hub via TCP, register, and block until ready."""
        self._conn_loop = asyncio.new_event_loop()
        self._conn_thread = threading.Thread(
            target=self._conn_loop.run_forever, daemon=True,
        )
        self._conn_thread.start()

        async def do_connect() -> None:
            self._reader, self._writer = await asyncio.open_connection(
                self.hub_tcp_host, self.hub_tcp_port,
            )
            self._reader._limit = 1024 * 1024
            self._setup_keepalive(self._writer.transport)
            self._parent_pid = os.getppid()
            logger.info(
                "Connected to Hub via TCP at {}:{}",
                self.hub_tcp_host, self.hub_tcp_port,
            )
            register_msg = {
                "type": "register",
                "channel": self.channel,
                "bot": self.bot,
                "pid": os.getpid(),
            }
            self._writer.write((json.dumps(register_msg) + "\n").encode())
            await self._writer.drain()
            resp_line = await self._reader.readline()
            resp = json.loads(resp_line.decode())
            if resp.get("success"):
                logger.info("Registered with Hub via TCP")
                await self._start_background_reader()
                self._parent_watch_task = asyncio.create_task(self._parent_watch_loop())
                # Startup notification is the subclass's responsibility — call
                # notify_ready() or override _send_startup_notification in start().
            else:
                raise RuntimeError(f"TCP registration failed: {resp}")

        future = asyncio.run_coroutine_threadsafe(do_connect(), self._conn_loop)
        future.result()


    async def _send_raw(self, data: dict[str, Any]) -> dict[str, Any]:
        """Low-level: write JSON dict to TCP, wait for response via background reader.

        Each call gets a unique sequence number embedded in the wire message.
        The hub echoes it back in the response, and _background_reader routes
        the response to the correct future.  This makes concurrent _send_raw
        calls safe — no shared slot to race on.

        No hard timeout — the caller (send_to_hub / async_send_to_hub) is
        responsible for bounding the total wait.  Hub death is detected by
        TCP keepalive + background reader EOF.
        """
        loop = asyncio.get_running_loop()
        seq = self._next_seq
        self._next_seq += 1
        future = loop.create_future()
        self._response_futures[seq] = future
        data["_seq"] = seq
        # Proactive size check: refuse to send messages > 1MB to hub
        payload_bytes = (json.dumps(data) + "\n").encode("utf-8")
        if len(payload_bytes) > 1024 * 1024:
            logger.error(
                "Outbound message too large ({} bytes), returning error to channel",
                len(payload_bytes),
            )
            self._response_futures.pop(seq, None)
            return {"success": False, "error": "Message too large (max 1MB), please shorten your input"}
        try:
            async with self._write_lock:
                self._writer.write(payload_bytes)
                await self._writer.drain()
            response = await future
            return response
        finally:
            self._response_futures.pop(seq, None)

    async def _send_message(self, msg: dict[str, Any]) -> HubResponse:
        """Send a message to Hub with one reconnect attempt on write failure.

        Reconnects only when the TCP write itself fails (stale connection).
        A timeout waiting for the response simply means the LLM is still
        processing — reconnecting would create duplicate work.

        Caught CancelledError means another concurrent caller triggered
        a reconnect and cancelled all pending futures — retry once on
        the fresh connection.
        """
        msg["type"] = "message"
        for attempt in range(2):
            try:
                resp = await self._send_raw(msg)
                return HubResponse.from_dict(resp)
            except asyncio.CancelledError:
                # Another concurrent caller's reconnect cancelled our future
                if attempt == 0:
                    continue
                raise
            except (ConnectionError, OSError) as e:
                if attempt == 0:
                    logger.warning("Send failed (attempt {}), reconnecting: {}", attempt + 1, e)
                    await self._reconnect()
                    continue
                raise
            except asyncio.TimeoutError:
                # LLM is still processing — don't reconnect, just propagate
                raise

    async def _reconnect(self) -> None:
        """Close the stale TCP connection and open a fresh one to Hub.

        This lets the proxy self-heal when the TCP connection goes bad
        (e.g. NAT timeout, intermediate device drop) without needing a
        full process restart.
        """
        logger.info("Reconnecting to Hub at {}:{}...", self.hub_tcp_host, self.hub_tcp_port)

        # Cancel all in-flight sends — the old connection is dead
        for fut in self._response_futures.values():
            if not fut.done():
                fut.cancel()
        self._response_futures.clear()

        # Cancel old background reader
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        # Close old writer
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

        # Open new connection
        self._reader, self._writer = await asyncio.open_connection(
            self.hub_tcp_host, self.hub_tcp_port,
        )
        self._reader._limit = 1024 * 1024
        self._setup_keepalive(self._writer.transport)

        # Re-register with Hub
        register_msg = {
            "type": "register",
            "channel": self.channel,
            "bot": self.bot,
            "pid": os.getpid(),
        }
        self._writer.write((json.dumps(register_msg) + "\n").encode())
        await self._writer.drain()
        resp_line = await self._reader.readline()
        resp = json.loads(resp_line.decode())
        if not resp.get("success"):
            raise RuntimeError(f"Re-registration with Hub failed: {resp}")

        # Restart background reader
        await self._start_background_reader()
        logger.info("Reconnected and re-registered with Hub")

    async def _background_reader(self) -> None:
        """Continuously read TCP messages and dispatch pushes or fulfill pending sends.

        Each message from hub carries ``_seq`` which matches the sequence number
        assigned in ``_send_raw``.  Responses are routed to the correct waiter;
        progress updates (no ``_seq`` or unknown seq) go to ``_handle_deliver``.

        On EOF (hub disconnected), cancels all pending sends and exits the process.
        """
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    # EOF — hub disconnected
                    logger.error("Hub TCP connection closed, exiting")
                    break
                data = json.loads(line.decode())

                if data.get("type") == "deliver":
                    seq = data.get("_seq")
                    if isinstance(seq, int) and seq in self._response_futures:
                        # Response to a pending _send_raw call
                        fut = self._response_futures.pop(seq)
                        if not fut.done():
                            fut.set_result(data)
                    else:
                        # Progress update (thinking, tool events) from hub
                        logger.debug("Background reader: deliver msg to chat={}", data.get("chat_id", "")[:20])
                        try:
                            await self._handle_deliver(data)
                        except Exception as e:
                            logger.error("Background reader: _handle_deliver failed: {}", e)
                    continue

                # Unexpected message type
                has_content = bool(data.get("content"))
                logger.warning(
                    "Background reader: unexpected msg (type={}, content={}): {}",
                    data.get("type", "none"),
                    has_content, str(data.get("content", ""))[:60],
                )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Background reader error: {}", e)
        finally:
            # Cancel any in-flight sends before exiting
            for fut in self._response_futures.values():
                if not fut.done():
                    fut.cancel()
            self._response_futures.clear()
            os._exit(1)

    async def _start_background_reader(self) -> None:
        """Start the background reader on the conn_loop."""
        self._reader_task = asyncio.create_task(self._background_reader())

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Handle a push delivery from hub. Override in subclasses to send messages."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        media = data.get("media", [])
        logger.info("Base _handle_deliver: chat={} content_len={} media_count={}",
                    chat_id, len(content) if content else 0, len(media))

    # ------------------------------------------------------------------
    # FIFO send queue infrastructure
    # ------------------------------------------------------------------

    def _send_worker_loop(self) -> None:
        """Daemon worker: dequeue items and dispatch to _process_send."""
        while True:
            item = self._send_queue.get()
            if item is None:
                break
            try:
                self._process_send(item)
            except Exception as e:
                logger.error("Send worker error in {}: {}", self.CHANNEL_NAME, e)

    def _enqueue_send(self, item: dict) -> None:
        """Enqueue a send item for FIFO processing. Thread-safe."""
        self._send_queue.put(item)

    def _process_send(self, item: dict) -> None:
        """Process a single send item. Runs on the send worker thread.

        Override in subclass to perform the actual outbound send.
        """
        raise NotImplementedError

    def _parent_alive(self) -> bool:
        """Check if parent (gateway) process is still alive.

        On Unix: getppid() returns 1 (init) when parent dies.
        On Windows: getppid() never changes, so use Win32 API.
        Returns False on error to ensure proxy exits when in doubt.
        """
        if os.name == "nt":
            import ctypes
            try:
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x0400, False, self._parent_pid)
                if handle:
                    try:
                        exit_code = ctypes.c_ulong()
                        kernel32.GetExitCodeProcess(
                            handle, ctypes.byref(exit_code),
                        )
                        return exit_code.value == 259  # STILL_ACTIVE
                    finally:
                        kernel32.CloseHandle(handle)
                return False
            except Exception as e:
                logger.warning("Failed to check parent process health: {}", e)
                return False
        return os.getppid() == self._parent_pid

    def _exit_if_disabled(self) -> None:
        """Re-read config file from disk and exit if this channel is disabled.

        Respects the user's intent when they toggle ``enabled: false``
        in config.json while the gateway is running.
        """
        import json
        config_path = os.environ.get("NANOBOT_CONFIG_PATH")
        if not config_path:
            return
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            ch = data.get("channels", {})
            if isinstance(ch, dict):
                ch = ch.get(self.channel, {})
            if not isinstance(ch, dict) or not ch.get("enabled", False):
                logger.warning("Channel {} disabled in config, exiting", self.channel)
                os._exit(0)
        except Exception as e:
            logger.debug("Failed to read config for {}: {}", self.channel, e)

    async def _parent_watch_loop(self) -> None:
        """Periodically check parent (gateway) is alive and channel is enabled.

        No TCP heartbeat — localhost connection doesn't need it.
        Hub death is detected by the background reader (EOF → proxy exits).
        Gateway death is detected via OS process table check on a 30s timer.
        """
        while True:
            await asyncio.sleep(30)
            if not self._parent_alive():
                logger.error("Gateway (parent) process died, exiting")
                os._exit(1)
            self._exit_if_disabled()

    # ------------------------------------------------------------------
    # Public send API
    # ------------------------------------------------------------------

    def send_to_hub(
        self, msg_data: dict[str, Any], timeout: int | None = None,
    ) -> HubResponse | None:
        """Thread-safe blocking send.  Returns None on permanent failure.

        No timeout and no serialization lock — multiple callers can send
        concurrently, each with their own seq-numbered future.  The hub
        always accepts messages regardless of how many are in flight.
        """
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_message(msg_data),
                self._conn_loop,
            )
            return future.result(timeout=timeout)
        except BaseException as e:
            logger.error("Failed to forward message: {}", e)
            return None

    async def async_send_to_hub(
        self, msg_data: dict[str, Any],
    ) -> HubResponse | None:
        """Async send.  Returns None on permanent failure.

        Runs the send on the conn_loop to guarantee cross-loop TCP safety,
        then bridges the result back to the caller's event loop.

        Intended for fully-async channels (slack, telegram, matrix).
        """
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_message(msg_data), self._conn_loop,
            )
            return await asyncio.wrap_future(future)
        except BaseException as e:
            logger.error("Failed to forward message: {}", e)
            return None

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def check_duplicate(self, msg_id: str, ttl: int = 300) -> bool:
        """Return True if msg_id was already processed within *ttl* seconds."""
        now = time.time()
        if msg_id in self._dedup and now - self._dedup[msg_id] < ttl:
            return True
        self._dedup[msg_id] = now
        # Prune expired entries when collection grows large
        if len(self._dedup) > 1000:
            cutoff = now - max(ttl, 300)
            self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}
        return False

    @staticmethod
    def _is_stale_message(create_time: float, max_age: float) -> bool:
        """Return True if the message's original creation time is too old.

        Args:
            create_time: Message creation Unix timestamp (seconds since epoch).
            max_age: Maximum allowed age in seconds.
        """
        if create_time is None or create_time <= 0:
            return False  # no valid timestamp, let it through
        age = time.time() - create_time
        if age > max_age:
            logger.warning("Dropping stale message (age={:.0f}s > max_age={:.0f}s)", age, max_age)
            return True
        return False

    # ------------------------------------------------------------------
    # Message builder
    # ------------------------------------------------------------------

    def build_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        message_id: str = "",
        media: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a standard message dict for sending to Hub."""
        return {
            "channel": self.channel,
            "bot": self.bot,
            "sender_id": sender_id,
            "chat_id": chat_id,
            "content": content,
            "message_id": message_id,
            "media": media or [],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

    def _workspace_dir(self) -> str:
        """Return the workspace directory, creating it if needed."""
        import pathlib
        ws = self.config.get("_workspace_path") or str(
            pathlib.Path.home() / ".nanobot" / "workspace"
        )
        pathlib.Path(ws).mkdir(parents=True, exist_ok=True)
        return ws

    def _save_media_bytes(self, filename: str, data: bytes) -> str:
        """Save bytes to ``<workspace>/incoming/`` and return the absolute path.

        Auto-renames if the filename already exists (appends ``_1``, ``_2``, etc.).
        """
        import pathlib
        incoming = pathlib.Path(self._workspace_dir()) / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)

        dest = incoming / filename
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = incoming / f"{stem}_{counter}{suffix}"
                counter += 1
        dest.write_bytes(data)
        return str(dest)

    @staticmethod
    def _scan_media_paths(content: str) -> list[tuple[str, str]]:
        """Scan content text for local media file references.

        Returns list of ``(local_path, media_type)`` where ``media_type``
        is ``"image"`` or ``"file"``.

        Recognised formats:
        - ``![alt](path)`` — markdown image → ``"image"``
        - ``[FILE]path[/FILE]`` — generic file marker → ``"file"``
        - Bare ``file://`` URIs → ``"image"`` or ``"file"`` by extension
        """
        import os
        import re

        results: list[tuple[str, str]] = []

        # Markdown images: ![alt](path)
        for m in re.finditer(r"!\[.*?\]\(([^)]+)\)", content):
            path = m.group(1).strip()
            if os.path.exists(path):
                ext = os.path.splitext(path)[1].lower()
                mtype = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp") else "file"
                results.append((path, mtype))

        # FILE markers: [FILE]path[/FILE]
        for m in re.finditer(r"\[FILE\](.*?)\[/FILE\]", content):
            path = m.group(1).strip()
            if os.path.exists(path):
                ext = os.path.splitext(path)[1].lower()
                mtype = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp") else "file"
                results.append((path, mtype))

        # file:// URIs
        for m in re.finditer(r"file:///([^\s\)\]}]+)", content):
            path = m.group(1).strip()
            if os.path.exists(path):
                ext = os.path.splitext(path)[1].lower()
                mtype = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp") else "file"
                results.append((path, mtype))

        return results

    @staticmethod
    def _media_text_reference(path: str) -> str:
        """Generate a text reference with absolute path for a media file.

        The LLM receives this text reference so it can locate the file on disk.
        """
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            return f"[用户发送了图片: {path}]"
        return f"[用户发送了文件: {path}]"

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_args() -> argparse.Namespace:
        """Parse standard CLI args shared by all proxy channels."""
        parser = argparse.ArgumentParser(
            description="nanobot proxy channel — connects to Hub via TCP",
        )
        parser.add_argument(
            "--hub-url", required=True,
            help="Hub API base URL (ignored, TCP is used)",
        )
        parser.add_argument(
            "--hub-tcp-port", required=True, type=int,
            help="Hub TCP port for proxy connections",
        )
        parser.add_argument("--channel", required=True, help="Channel name")
        parser.add_argument("--bot", required=True, help="Bot name")
        return parser.parse_args()

    @staticmethod
    def get_config() -> dict[str, Any]:
        """Read channel config from environment variable (set by ProxyManager)."""
        return json.loads(os.environ.get("NANOBOT_PROXY_CONFIG", "{}"))

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        """Check required config fields and exit via sys.exit(1) if missing."""
        missing = [f for f in cls.REQUIRED_CONFIG_FIELDS if not config.get(f)]
        if missing:
            logger.error(
                "{} proxy: missing required config: {}",
                cls.CHANNEL_NAME, missing,
            )
            sys.exit(1)

    @classmethod
    def run_main(cls) -> None:
        """Standard entry point for all proxy channels."""
        args = cls.parse_args()
        config = cls.get_config()
        cls.validate_config(config)

        logger.info("{} proxy starting for {}:{}", cls.CHANNEL_NAME, args.channel, args.bot)

        try:
            proxy = cls(
                config=config,
                hub_tcp_host="127.0.0.1",
                hub_tcp_port=args.hub_tcp_port,
                channel=args.channel,
                bot=args.bot,
            )
            proxy.connect_to_hub()
            proxy.start()
        except KeyboardInterrupt:
            logger.info("{} proxy stopped via KeyboardInterrupt", cls.CHANNEL_NAME)
            os._exit(0)
        except Exception as e:
            logger.error("Failed to start {} proxy: {}", cls.CHANNEL_NAME, e)
            sys.exit(1)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Enter the channel's own message listening loop."""
        raise NotImplementedError

    def send_reply(self, chat_id: str, reply_to: str, content: str) -> None:
        """Send a text reply back through the channel."""
        raise NotImplementedError
