"""Matrix proxy - runs as a separate process, connects to Matrix via nio and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class MatrixProxyChannel(BaseProxyChannel):
    """Handles Matrix message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "Matrix"
    REQUIRED_CONFIG_FIELDS = ["homeserver", "user_id", "password"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._client: Any = None
        self._matrix_loop: asyncio.AbstractEventLoop | None = None

    async def _on_message(self, room: Any, event: Any) -> None:
        try:
            if not hasattr(event, "body") or not event.body:
                return

            msg_id = getattr(event, "event_id", "") or str(event)
            if self.check_duplicate(msg_id):
                return

            sender_id = getattr(event, "sender", "unknown")
            chat_id = getattr(room, "room_id", "unknown")
            content = event.body

            msg_data = self.build_message(sender_id, chat_id, content, msg_id)
            await self.async_send_to_hub(msg_data)

        except Exception as e:
            logger.error("Matrix proxy message handler error: {}", e)

    def start(self) -> None:
        """Run the Matrix sync connection."""
        from nio import AsyncClient, AsyncClientConfig, RoomMessage, RoomMessageText

        homeserver = self.config.get("homeserver", "https://matrix.org")
        user_id = self.config.get("user_id", "")
        password = self.config.get("password", "")
        device_id = self.config.get("device_id", "nanobot")

        self._client = AsyncClient(
            AsyncClientConfig(ignore_device_verification=True),
            homeserver,
            user_id,
        )
        self._client.add_event_callback(
            self._on_message,
            (RoomMessage, RoomMessageText),
        )

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._matrix_loop = loop

        async def login_and_sync():
            await self._client.login(password, device_id=device_id)
            await self._client.sync_forever()

        loop.run_until_complete(login_and_sync())

    def _process_send(self, item: dict) -> None:
        """Send queued message to Matrix via async bridge."""
        if not self._client or not self._matrix_loop:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._client.room_send(
                    item["chat_id"],
                    "m.room.message",
                    {"msgtype": "m.text", "body": item["content"]},
                ),
                self._matrix_loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.error("Matrix send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Matrix room."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({"chat_id": chat_id, "content": content})


def main() -> None:
    MatrixProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Matrix proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
