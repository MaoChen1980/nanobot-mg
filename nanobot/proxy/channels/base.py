"""Base class for all proxy channels — handles TCP communication with Hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._conn_loop: asyncio.AbstractEventLoop | None = None
        self._conn_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        # msg_id -> timestamp for deduplication
        self._dedup: dict[str, float] = {}

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
            else:
                raise RuntimeError(f"TCP registration failed: {resp}")

        future = asyncio.run_coroutine_threadsafe(do_connect(), self._conn_loop)
        future.result()

    async def _do_send(self, msg: dict[str, Any]) -> HubResponse:
        """Raw send to Hub — no retry logic."""
        msg["type"] = "message"
        self._writer.write((json.dumps(msg) + "\n").encode())
        await self._writer.drain()
        resp_line = await self._reader.readline()
        return HubResponse.from_dict(json.loads(resp_line.decode()))

    async def _reconnect_to_hub(self, max_retries: int = 3) -> bool:
        """Reconnect to Hub with exponential backoff. Runs on conn_loop."""
        for attempt in range(1, max_retries + 1):
            try:
                if self._writer and not self._writer.is_closing():
                    self._writer.close()
                    try:
                        await self._writer.wait_closed()
                    except Exception:
                        pass

                self._reader, self._writer = await asyncio.open_connection(
                    self.hub_tcp_host, self.hub_tcp_port,
                )
                logger.info("Reconnected to Hub via TCP (attempt {})", attempt)

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
                    logger.info("Re-registered with Hub via TCP (attempt {})", attempt)
                    return True
            except Exception as e:
                logger.warning(
                    "Reconnect attempt {}/{} failed: {}",
                    attempt, max_retries, e,
                )

            if attempt < max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        return False

    async def _send_with_reconnect(self, msg: dict[str, Any]) -> HubResponse:
        """Send with automatic reconnect on failure."""
        last_error = None
        for attempt in range(3):
            try:
                return await self._do_send(msg)
            except Exception as e:
                last_error = e
                logger.warning("Send attempt {}/3 failed: {}", attempt + 1, e)
                if attempt < 2:
                    if not await self._reconnect_to_hub():
                        break
        raise RuntimeError(f"Send failed after 3 attempts: {last_error}")

    # ------------------------------------------------------------------
    # Public send API
    # ------------------------------------------------------------------

    def send_to_hub(
        self, msg_data: dict[str, Any], timeout: int = 300,
    ) -> HubResponse | None:
        """Thread-safe blocking send.  Self-terminates on permanent failure.

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
            logger.error(
                "Failed to forward message after retries: {}, exiting process", e,
            )
            os._exit(1)

    async def async_send_to_hub(
        self, msg_data: dict[str, Any],
    ) -> HubResponse | None:
        """Async send.  Self-terminates on permanent failure.

        Intended for fully-async channels (slack, telegram, matrix).
        """
        try:
            return await self._send_with_reconnect(msg_data)
        except Exception as e:
            logger.error(
                "Failed to forward message after retries: {}, exiting process", e,
            )
            os._exit(1)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def check_duplicate(self, msg_id: str, ttl: int = 300) -> bool:
        """Return True if msg_id was already processed within *ttl* seconds."""
        now = time.time()
        if msg_id in self._dedup and now - self._dedup[msg_id] < ttl:
            return True
        self._dedup[msg_id] = now
        # Prune once in a while
        if len(self._dedup) > 1000:
            cutoff = now - ttl
            self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}
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
    ) -> dict[str, Any]:
        """Build a standard message dict for sending to Hub."""
        return {
            "channel": self.channel,
            "bot": self.bot,
            "sender_id": sender_id,
            "chat_id": chat_id,
            "content": content,
            "message_id": message_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
        """Check required config fields, exit if missing."""
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
