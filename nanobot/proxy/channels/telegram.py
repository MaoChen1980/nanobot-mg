"""Telegram proxy - runs as a separate process, connects to Telegram via python-telegram-bot and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import sys
import time
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class TelegramProxyChannel(BaseProxyChannel):
    """Handles Telegram message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "Telegram"
    REQUIRED_CONFIG_FIELDS = ["token"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._app: Any = None

    async def _handle_update(self, update: Any, context: Any) -> None:
        try:
            msg = update.message or update.edited_message
            if not msg or not msg.text:
                return

            msg_id = str(msg.message_id)
            if self.check_duplicate(msg_id):
                return

            sender_id = str(msg.from_user.id)
            chat_id = str(msg.chat.id)
            content = msg.text.strip()

            msg_data = self.build_message(sender_id, chat_id, content, msg_id)
            response = await self.async_send_to_hub(msg_data)

            if response and response.success and response.content:
                await msg.reply_text(response.content)

        except Exception as e:
            logger.error("Telegram proxy handler error: {}", e)

    def start(self) -> None:
        """Run the Telegram bot polling."""
        from telegram.ext import Application, MessageHandler, filters

        token = self.config.get("token", "")
        if not token:
            logger.error("Telegram proxy: token required in config")
            sys.exit(1)

        self._app = Application.builder().token(token).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_update)
        )

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._app.run_polling())


def main() -> None:
    TelegramProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Telegram proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
