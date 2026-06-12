"""WhatsApp proxy - runs as a separate process, connects to Node.js bridge and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class WhatsAppProxyChannel(BaseProxyChannel):
    """Handles WhatsApp message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "WhatsApp"
    REQUIRED_CONFIG_FIELDS = []

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._ws: Any = None
        self._bridge_loop: asyncio.AbstractEventLoop | None = None

    def _on_bridge_message(self, data: dict[str, Any]) -> None:
        try:
            msg_type = data.get("type", "")
            if msg_type != "message":
                return

            msg_id = data.get("message_id", "")
            if self.check_duplicate(msg_id):
                return

            content = data.get("content", "").strip()
            media = data.get("media")
            sender_id = data.get("sender_id", "")
            chat_id = data.get("chat_id", "")

            # If no text content but media exists, create a text reference
            if not content and media:
                content = self._media_text_reference(media[0])

            if not content:
                return

            msg_data = self.build_message(sender_id, chat_id, content, msg_id, media=media)
            self.send_to_hub(msg_data)

        except Exception as e:
            logger.error("WhatsApp proxy message handler error: {}", e)

    def _send_bridge_text(self, chat_id: str, content: str) -> None:
        if not self._ws or not self._bridge_loop:
            return
        try:
            msg = json.dumps({"type": "send", "chat_id": chat_id, "content": content})
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._bridge_loop)
        except Exception as e:
            logger.error("WhatsApp bridge send error: {}", e)

    def _process_send(self, item: dict) -> None:
        """Send queued message to WhatsApp via async bridge."""
        if not self._ws or not self._bridge_loop:
            return
        try:
            media_paths = self._scan_media_paths(item["content"])
            if media_paths:
                path, mtype = media_paths[0]
                _MIME_MAP = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                }
                ext = os.path.splitext(path)[1].lower()
                mimetype = _MIME_MAP.get(ext, "application/octet-stream")
                msg = json.dumps({
                    "type": "send_media",
                    "chat_id": item["chat_id"],
                    "filePath": path,
                    "mimetype": mimetype,
                })
            else:
                msg = json.dumps({
                    "type": "send",
                    "chat_id": item["chat_id"],
                    "content": item["content"],
                })
            future = asyncio.run_coroutine_threadsafe(
                self._ws.send(msg), self._bridge_loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.error("WhatsApp send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to WhatsApp chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({"chat_id": chat_id, "content": content})

    def start(self) -> None:
        """Run the WhatsApp bridge WebSocket connection."""
        import websockets

        bridge_url = self.config.get("bridge_url", "ws://localhost:3001")
        bridge_token = self.config.get("bridge_token", "")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._bridge_loop = loop

        async def connect_bridge():
            headers = {}
            if bridge_token:
                headers["Authorization"] = f"Bearer {bridge_token}"

            self._ws = await websockets.connect(bridge_url, extra_headers=headers)
            logger.info("WhatsApp bridge connected")

            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                    self._on_bridge_message(data)
                except Exception as e:
                    logger.warning("WhatsApp bridge message error: {}", e)

        try:
            loop.run_until_complete(connect_bridge())
        except Exception as e:
            logger.error("WhatsApp bridge connection error: {}", e)


def main() -> None:
    WhatsAppProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WhatsApp proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
