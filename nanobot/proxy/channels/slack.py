"""Slack proxy - runs as a separate process, connects to Slack via Socket Mode and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import sys
import time
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class SlackProxyChannel(BaseProxyChannel):
    """Handles Slack message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "Slack"
    REQUIRED_CONFIG_FIELDS = ["bot_token", "app_token"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._web_client: Any = None
        self._socket_client: Any = None
        self._bot_user_id: str | None = None
        self._processed: dict[str, float] = {}

    async def _on_socket_request(self, client: Any, req: Any) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        if req.type == "interactive":
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            payload = req.payload or {}
            actions = payload.get("actions") or []
            if not actions:
                return
            value = str(actions[0].get("value") or "")
            user_info = payload.get("user") or {}
            sender_id = str(user_info.get("id") or "")
            channel_info = payload.get("channel") or {}
            chat_id = str(channel_info.get("id") or "")
            if not sender_id or not chat_id or not value:
                return
            await self._handle_text_message(sender_id, chat_id, value, req)
            return

        if req.type != "events_api":
            return

        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = event.get("type")

        if event_type not in ("message", "app_mention"):
            return

        sender_id = event.get("user")
        chat_id = event.get("channel")
        if not sender_id or not chat_id:
            return

        if self._bot_user_id and sender_id == self._bot_user_id:
            return

        subtype = event.get("subtype")
        if subtype and subtype != "file_share":
            return

        text = event.get("text") or ""
        if not text:
            return

        await self._handle_text_message(sender_id, chat_id, text, req)

    async def _handle_text_message(self, sender_id: str, chat_id: str, text: str, req: Any) -> None:
        now = time.time()
        key = f"{chat_id}:{text[:50]}"
        if key in self._processed and now - self._processed[key] < 2:
            return
        self._processed[key] = now

        msg_data = self.build_message(sender_id, chat_id, text, req.envelope_id or f"{chat_id}:{now}")
        response = await self.async_send_to_hub(msg_data)

        if response and response.success and response.content:
            await self._web_client.chat_postMessage(
                channel=chat_id,
                text=response.content,
            )

    def start(self) -> None:
        """Run the Slack Socket Mode connection."""
        from slack_sdk.socket_mode.websockets import SocketModeClient
        from slack_sdk.web.async_client import AsyncWebClient

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        self._web_client = AsyncWebClient(token=self.config.get("bot_token", ""))
        self._socket_client = SocketModeClient(
            app_token=self.config.get("app_token", ""),
            web_client=self._web_client,
        )

        self._socket_client.socket_mode_request_listeners.append(
            lambda c, r: asyncio.run_coroutine_threadsafe(
                self._on_socket_request(c, r), loop
            )
        )

        loop.run_until_complete(self._socket_client.connect())
        loop.run_forever()


def main() -> None:
    SlackProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Slack proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
