"""Base class for all proxy channels — handles TCP communication with Hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
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
        self._msg_send_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._parent_watch_task: asyncio.Task | None = None
        self._pending_response: asyncio.Future | None = None
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


    async def _do_send(self, msg: dict[str, Any]) -> HubResponse:
        """Raw send to Hub — no retry logic."""
        msg["type"] = "message"
        resp = await self._send_raw(msg)
        return HubResponse.from_dict(resp)

    async def _send_raw(self, data: dict[str, Any]) -> dict[str, Any]:
        """Low-level: write JSON dict to TCP, wait for response via background reader.

        The TCP write is serialized through _write_lock (microseconds).  The
        response wait happens without any lock so message sends and heartbeats
        don't block each other.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_response = future
        try:
            async with self._write_lock:
                self._writer.write((json.dumps(data) + "\n").encode())
                await self._writer.drain()
            response = await asyncio.wait_for(future, timeout=120)
            return response
        finally:
            self._pending_response = None

    async def _background_reader(self) -> None:
        """Continuously read TCP messages and dispatch pushes or fulfill pending responses.

        Routes responses by type:
        - ``deliver``    → message _pending_response (if "success") or _handle_deliver
        - other          → _pending_response (fallback for unexpected data)

        On EOF (hub disconnected), cancels any pending response and exits the process.
        """
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    # EOF — hub disconnected
                    logger.error("Hub TCP connection closed, exiting")
                    break
                data = json.loads(line.decode())

                # 1. Deliver from hub — message response (has "success") or progress update
                if data.get("type") == "deliver":
                    if "success" in data and self._pending_response is not None and not self._pending_response.done():
                        self._pending_response.set_result(data)
                    else:
                        logger.debug("Background reader: deliver msg to chat={}", data.get("chat_id", "")[:20])
                        await self._handle_deliver(data)
                    continue

                # 2. Unexpected response type — fulfill pending response if any
                if self._pending_response is not None and not self._pending_response.done():
                    logger.trace("Background reader: fulfill pending response")
                    self._pending_response.set_result(data)
                else:
                    has_content = bool(data.get("content"))
                    logger.warning(
                        "Background reader: dropped msg (type={}, pending={}, content={}): {}",
                        data.get("type", "none"), self._pending_response is not None,
                        has_content, str(data.get("content", ""))[:60],
                    )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Background reader error: {}", e)
        finally:
            # Cancel any in-flight send before exiting
            if self._pending_response is not None and not self._pending_response.done():
                self._pending_response.cancel()
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

    async def _send_with_reconnect(self, msg: dict[str, Any]) -> HubResponse:
        """Send with retry on transient failure.  No reconnect — hub death = proxy exit."""
        async with self._msg_send_lock:
            last_error = None
            for attempt in range(3):
                try:
                    return await self._do_send(msg)
                except Exception as e:
                    last_error = e
                    logger.warning("Send attempt {}/3 failed: {}", attempt + 1, e)
                    await asyncio.sleep(0.5 * (attempt + 1))
            raise RuntimeError(f"Send failed after 3 attempts: {last_error}")

    # ------------------------------------------------------------------
    # Public send API
    # ------------------------------------------------------------------

    def send_to_hub(
        self, msg_data: dict[str, Any], timeout: int = 300,
    ) -> HubResponse | None:
        """Thread-safe blocking send.  Returns None on permanent failure.

        Intended for callback/polling-based channels (feishu, dingtalk, …).
        """
        try:
            with self._send_lock:
                future = asyncio.run_coroutine_threadsafe(
                    self._send_with_reconnect(msg_data),
                    self._conn_loop,
                )
                return future.result(timeout=timeout)
        except Exception as e:
            logger.error("Failed to forward message after retries: {}", e)
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
                self._send_with_reconnect(msg_data), self._conn_loop,
            )
            return await asyncio.wrap_future(future)
        except Exception as e:
            logger.error("Failed to forward message after retries: {}", e)
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
