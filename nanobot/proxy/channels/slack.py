"""Slack proxy - runs as a separate process, connects to Slack via Socket Mode and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import os
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
        self._slack_loop: asyncio.AbstractEventLoop | None = None

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

        # Handle file attachments — download each via private URL with bot token
        media_paths: list[str] = []
        files = event.get("files") or []
        for f in files:
            download_url = f.get("url_private_download")
            if not download_url:
                continue
            try:
                import httpx
                headers = {"Authorization": f"Bearer {self.config.get('bot_token', '')}"}
                resp = httpx.get(download_url, headers=headers, follow_redirects=True, timeout=60)
                resp.raise_for_status()
                filename = f.get("name") or f.get("title") or f"slack_file_{f.get('id', 'unknown')}"
                local_path = self._save_media_bytes(filename, resp.content)
                media_paths.append(local_path)
            except Exception as e:
                logger.error("Slack file download failed: {}", e)

        if not text and not media_paths:
            return

        await self._handle_text_message(sender_id, chat_id, text, req, media=media_paths)

    async def _handle_text_message(self, sender_id: str, chat_id: str, text: str, req: Any, media: list[str] | None = None) -> None:
        # Use envelope_id for dedup (5 minute window for Slack's at-least-once delivery)
        if self.check_duplicate(req.envelope_id or f"{chat_id}:{time.time()}", ttl=300):
            return

        msg_data = self.build_message(sender_id, chat_id, text, req.envelope_id or f"{chat_id}:{time.time()}", media=media or [])
        await self.async_send_to_hub(msg_data)

    def start(self) -> None:
        """Run the Slack Socket Mode connection."""
        from slack_sdk.socket_mode.websockets import SocketModeClient
        from slack_sdk.web.async_client import AsyncWebClient

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._slack_loop = loop

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

    def _process_send(self, item: dict) -> None:
        """Send queued message to Slack via async bridge."""
        if not self._web_client or not self._slack_loop:
            return
        try:
            chat_id = item["chat_id"]
            content = item.get("content", "")
            media_list = list(item.get("media", []))
            # Also scan content for embedded media paths (e.g. [FILE]path[/FILE])
            if not media_list and content:
                media_list = [p for p, _ in self._scan_media_paths(content)]

            # Upload media files first
            for path in media_list:
                if not os.path.exists(path):
                    logger.warning("Slack media send: file not found: {}", path)
                    continue
                future = asyncio.run_coroutine_threadsafe(
                    self._web_client.files_upload_v2(
                        channel=chat_id,
                        file=path,
                        title=os.path.basename(path),
                    ),
                    self._slack_loop,
                )
                future.result(timeout=30)

            # Send text content after media
            if content:
                future = asyncio.run_coroutine_threadsafe(
                    self._web_client.chat_postMessage(channel=chat_id, text=content),
                    self._slack_loop,
                )
                future.result(timeout=30)
        except Exception as e:
            logger.error("Slack send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Slack channel."""
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
    SlackProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Slack proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
