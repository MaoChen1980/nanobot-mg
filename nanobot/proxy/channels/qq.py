"""QQ proxy - runs as a separate process, connects to QQ via botpy SDK and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import sys
import time
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
    REQUIRED_CONFIG_FIELDS = ["app_id", "secret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._processed_ids: set[str] = set()
        self._chat_type_cache: dict[str, str] = {}
        self._client: Any = None
        self._msg_seq: int = 1

    async def _on_message(self, data: Any, is_group: bool = False) -> None:
        try:
            if data.id in self._processed_ids:
                return
            self._processed_ids.add(data.id)
            if len(self._processed_ids) > 1000:
                self._processed_ids = set(list(self._processed_ids)[-500:])

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
                await self._send_reply(chat_id, is_group, response.content)

        except Exception as e:
            logger.error("QQ proxy message handler error: {}", e)

    async def _send_reply(self, chat_id: str, is_group: bool, content: str) -> None:
        if not self._client:
            return
        try:
            self._msg_seq += 1
            payload = {"msg_type": 2 if self.config.get("msg_format") == "markdown" else 0, "content": content}
            if is_group:
                await self._client.api.post_group_message(group_openid=chat_id, **payload)
            else:
                await self._client.api.post_c2c_message(openid=chat_id, **payload)
        except Exception as e:
            logger.error("QQ reply error: {}", e)

    def start(self) -> None:
        """Run the QQ bot connection."""
        import botpy

        intents = botpy.Intents(public_messages=True, direct_message=True)

        class Bot(botpy.Client):
            async def on_ready(self):
                logger.info("QQ proxy bot ready: {}", self.robot.name)

            async def on_c2c_message_create(self, message):
                await self._on_message(message, is_group=False)

            async def on_group_at_message_create(self, message):
                await self._on_message(message, is_group=True)

        self._client = Bot(intents=intents, ext_handlers=False)
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            self._client.start(appid=self.config.get("app_id", ""), secret=self.config.get("secret", ""))
        )


def main() -> None:
    QQProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("QQ proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
