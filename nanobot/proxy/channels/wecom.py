"""WeCom proxy - runs as a separate process, connects to WeCom WebSocket and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class WecomProxyChannel(BaseProxyChannel):
    """Handles WeCom message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "WeCom"
    REQUIRED_CONFIG_FIELDS = ["bot_id", "secret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._client: Any = None
        self._chat_frames: dict[str, Any] = {}

    def _process_message(self, frame: Any, msg_type: str) -> None:
        try:
            body = frame.body if hasattr(frame, "body") else (frame.get("body") if isinstance(frame, dict) else frame)
            if not isinstance(body, dict):
                return

            msg_id = body.get("msgid") or f"{body.get('chatid', '')}_{body.get('sendertime', '')}"
            if self.check_duplicate(msg_id):
                return

            from_info = body.get("from", {})
            sender_id = from_info.get("userid", "unknown") if isinstance(from_info, dict) else "unknown"

            chat_id = body.get("chatid", sender_id)

            content_parts = []
            if msg_type == "text":
                text = body.get("text", {}) if isinstance(body.get("text"), dict) else {}
                text_content = text.get("content", "")
                if text_content:
                    content_parts.append(text_content)
            elif msg_type == "voice":
                voice = body.get("voice", {}) if isinstance(body.get("voice"), dict) else {}
                voice_content = voice.get("content", "")
                if voice_content:
                    content_parts.append(f"[voice] {voice_content}")

            content = "\n".join(content_parts) if content_parts else ""
            if not content:
                return

            self._chat_frames[chat_id] = frame

            msg_data = self.build_message(sender_id, chat_id, content, msg_id)
            response = self.send_to_hub(msg_data)

            if response and response.success and response.content:
                self._enqueue_send({"frame": frame, "content": response.content})

        except Exception as e:
            logger.error("WeCom proxy message handler error: {}", e)

    def _send_reply(self, frame: Any, content: str) -> None:
        if not self._client:
            return
        try:
            stream_id = self._generate_req_id("stream")
            self._client.reply_stream(frame, stream_id, content, finish=True)
        except Exception as e:
            logger.error("WeCom reply error: {}", e)

    def _process_send(self, item: dict) -> None:
        """Send queued message to WeCom."""
        self._send_reply(item["frame"], item["content"])

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to WeCom chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            frame = self._chat_frames.get(chat_id)
            if frame:
                self._enqueue_send({"frame": frame, "content": content})

    @staticmethod
    def _generate_req_id(prefix: str) -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def start(self) -> None:
        """Run the WeCom WebSocket connection."""
        from wecom_aibot_sdk import WSClient

        client = WSClient({
            "bot_id": self.config.get("bot_id", ""),
            "secret": self.config.get("secret", ""),
            "reconnect_interval": 1000,
            "max_reconnect_attempts": -1,
            "heartbeat_interval": 30000,
        })
        self._client = client

        client.on("connected", lambda f: logger.info("WeCom WebSocket connected"))
        client.on("authenticated", lambda f: logger.info("WeCom authenticated"))
        client.on("disconnected", lambda f: logger.warning("WeCom WebSocket disconnected"))
        client.on("message.text", lambda f: self._process_message(f, "text"))
        client.on("message.image", lambda f: self._process_message(f, "image"))
        client.on("message.voice", lambda f: self._process_message(f, "voice"))
        client.on("message.file", lambda f: self._process_message(f, "file"))
        client.on("message.mixed", lambda f: self._process_message(f, "mixed"))

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.connect_async())
        except Exception as e:
            logger.error("WeCom WS error: {}", e)
        finally:
            loop.close()


def main() -> None:
    WecomProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WeCom proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
