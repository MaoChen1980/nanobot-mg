"""WhatsApp proxy - runs as a separate process, connects to Node.js bridge and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class WhatsAppProxyChannel(BaseProxyChannel):
    """Handles WhatsApp message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "WhatsApp"
    REQUIRED_CONFIG_FIELDS = []

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._processed: dict[str, float] = {}
        self._ws: Any = None

    def _on_bridge_message(self, data: dict[str, Any]) -> None:
        try:
            msg_type = data.get("type", "")
            if msg_type != "message":
                return

            msg_id = data.get("message_id", "")
            now = time.time()
            if msg_id in self._processed:
                return
            self._processed[msg_id] = now
            self._processed = {k: v for k, v in self._processed.items() if now - v < 300}

            content = data.get("content", "").strip()
            if not content:
                return

            sender_id = data.get("sender_id", "")
            chat_id = data.get("chat_id", "")

            msg_data = self.build_message(sender_id, chat_id, content, msg_id)
            response = self.send_to_hub(msg_data)

            if response and response.success and response.content:
                self._send_bridge_text(chat_id, response.content)

        except Exception as e:
            logger.error("WhatsApp proxy message handler error: {}", e)

    def _send_bridge_text(self, chat_id: str, content: str) -> None:
        if not self._ws:
            return
        try:
            msg = json.dumps({"type": "send", "chat_id": chat_id, "content": content})
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._conn_loop)
        except Exception as e:
            logger.error("WhatsApp bridge send error: {}", e)

    def start(self) -> None:
        """Run the WhatsApp bridge WebSocket connection."""
        import websockets

        bridge_url = self.config.get("bridge_url", "ws://localhost:3001")
        bridge_token = self.config.get("bridge_token", "")

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

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(connect_bridge())
        except Exception as e:
            logger.error("WhatsApp bridge connection error: {}", e)
        finally:
            loop.close()


def main() -> None:
    WhatsAppProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WhatsApp proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
