"""Telegram proxy - runs as a separate process, connects to Telegram via python-telegram-bot and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import os
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
        self._telegram_loop: asyncio.AbstractEventLoop | None = None

    async def _handle_update(self, update: Any, context: Any) -> None:
        try:
            msg = update.message or update.edited_message
            if not msg:
                return

            msg_id = str(msg.message_id)
            if self.check_duplicate(msg_id):
                return

            sender_id = str(msg.from_user.id)
            chat_id = str(msg.chat.id)

            media_paths: list[str] = []
            content = ""

            if msg.photo:
                file = await msg.photo[-1].get_file()
                photo_bytes = await file.download_as_bytearray()
                local_path = self._save_media_bytes(f"telegram_photo_{msg_id}.jpg", bytes(photo_bytes))
                media_paths.append(local_path)
                content = self._media_text_reference(local_path)
            elif msg.document:
                file = await msg.document.get_file()
                doc_bytes = await file.download_as_bytearray()
                original_name = msg.document.file_name or f"telegram_doc_{msg_id}"
                local_path = self._save_media_bytes(original_name, bytes(doc_bytes))
                media_paths.append(local_path)
                content = self._media_text_reference(local_path)
            elif msg.text:
                content = msg.text.strip()
            else:
                return

            msg_data = self.build_message(sender_id, chat_id, content, msg_id, media=media_paths)
            response = await self.async_send_to_hub(msg_data)

            if response and response.success and (response.content or response.media):
                enqueue_item: dict[str, Any] = {"chat_id": chat_id}
                if response.content:
                    enqueue_item["content"] = response.content
                if response.media:
                    enqueue_item["media"] = response.media
                self._enqueue_send(enqueue_item)

        except Exception as e:
            logger.error("Telegram proxy handler error: {}", e)

    def start(self) -> None:
        """Run the Telegram bot polling."""
        from telegram.ext import Application, MessageHandler, filters

        token = self.config.get("token", "")
        if not token:
            logger.error("Telegram proxy: token required in config")
            sys.exit(1)

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._telegram_loop = loop

        self._app = Application.builder().token(token).loop(loop).build()
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL,
                self._handle_update,
            )
        )

        loop.run_until_complete(self._app.run_polling())

    def _process_send(self, item: dict) -> None:
        """Send queued message to Telegram via async bridge."""
        if not self._app or not self._telegram_loop:
            return
        try:
            chat_id = item["chat_id"]
            content = item.get("content", "")
            media_list = item.get("media", [])

            # Send media files first
            for path in media_list:
                if not os.path.exists(path):
                    logger.warning("Telegram media send: file not found: {}", path)
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    future = asyncio.run_coroutine_threadsafe(
                        self._app.bot.send_photo(chat_id=chat_id, photo=open(path, "rb")),
                        self._telegram_loop,
                    )
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        self._app.bot.send_document(chat_id=chat_id, document=open(path, "rb")),
                        self._telegram_loop,
                    )
                future.result(timeout=30)

            # Send text content after media
            if content:
                future = asyncio.run_coroutine_threadsafe(
                    self._app.bot.send_message(chat_id=chat_id, text=content),
                    self._telegram_loop,
                )
                future.result(timeout=30)
        except Exception as e:
            logger.error("Telegram send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Telegram chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        media = data.get("media", [])
        if chat_id and (content or media):
            enqueue_item: dict[str, Any] = {"chat_id": chat_id}
            if content:
                enqueue_item["content"] = content
            if media:
                enqueue_item["media"] = media
            self._enqueue_send(enqueue_item)


def main() -> None:
    TelegramProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Telegram proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
