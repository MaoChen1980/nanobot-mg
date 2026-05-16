"""QQ proxy - runs as a separate process, connects to QQ via botpy SDK and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel

try:
    import botpy
    QQ_AVAILABLE = True
except ImportError:
    QQ_AVAILABLE = False


class QQProxyChannel(BaseProxyChannel):
    """Handles QQ message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "QQ"
    REQUIRED_CONFIG_FIELDS = ["appId", "secret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._chat_type_cache: dict[str, str] = {}
        self._client: Any = None
        self._msg_seq: int = 1
        self._qq_loop: asyncio.AbstractEventLoop | None = None

    async def _on_message(self, data: Any, is_group: bool = False) -> None:
        try:
            if self.check_duplicate(str(data.id)):
                return

            if is_group:
                chat_id = data.group_openid
                user_id = data.author.member_openid
                self._chat_type_cache[chat_id] = "group"
            else:
                chat_id = str(getattr(data.author, "id", None) or getattr(data.author, "user_openid", "unknown"))
                user_id = chat_id
                self._chat_type_cache[chat_id] = "c2c"

            content = (data.content or "").strip()
            if not content:
                return

            msg_data = self.build_message(user_id, chat_id, content, data.id)
            response = await self.async_send_to_hub(msg_data)

            if response and response.success and response.content:
                self._enqueue_send({"chat_id": chat_id, "is_group": is_group, "content": response.content})

        except Exception as e:
            logger.error("QQ proxy message handler error: {}", e)

    async def _send_reply(self, chat_id: str, is_group: bool, content: str) -> None:
        if not self._client:
            return
        try:
            self._msg_seq += 1
            payload = {"msg_type": 2 if self.config.get("msgFormat") == "markdown" else 0, "content": content}
            if is_group:
                await self._client.api.post_group_message(group_openid=chat_id, **payload)
            else:
                await self._client.api.post_c2c_message(openid=chat_id, **payload)
        except Exception as e:
            logger.error("QQ reply error: {}", e)

    def start(self) -> None:
        """Run the QQ bot connection on a separate thread with its own event loop."""
        import asyncio
        import threading

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._qq_loop = loop

        intents = botpy.Intents(public_messages=True, direct_message=True)

        _channel = self

        class Bot(botpy.Client):
            async def on_ready(self):
                logger.info("QQ proxy bot ready: {}", self.robot.name)

            async def on_c2c_message_create(self, message):
                await _channel._on_message(message, is_group=False)

            async def on_group_at_message_create(self, message):
                await _channel._on_message(message, is_group=True)

        self._client = Bot(intents=intents)

        def run_bot():
            loop.run_until_complete(
                self._client.start(appid=str(self.config.get("appId", "")), secret=self.config.get("secret", ""))
            )

        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()

        while True:
            import time
            time.sleep(5)

    def _process_send(self, item: dict) -> None:
        """Send queued message to QQ via async bridge."""
        if not self._client or not self._qq_loop:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_reply(item["chat_id"], item["is_group"], item["content"]),
                self._qq_loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.error("QQ send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to QQ chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content and self._client and self._qq_loop:
            is_group = self._chat_type_cache.get(chat_id) == "group"
            self._enqueue_send({"chat_id": chat_id, "is_group": is_group, "content": content})


def main() -> None:
    QQProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("QQ proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
