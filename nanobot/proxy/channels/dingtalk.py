"""DingTalk proxy - runs as a separate process, connects to DingTalk Stream SDK and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class DingTalkProxyChannel(BaseProxyChannel):
    """Handles DingTalk message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "DingTalk"
    REQUIRED_CONFIG_FIELDS = ["client_id", "client_secret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._processed: dict[str, float] = {}

    def on_message(self, data: Any) -> None:
        """Sync callback from DingTalk SDK - forward message to Hub."""
        logger.info("DingTalk on_message called")
        try:
            from dingtalk_stream.chatbot import ChatbotMessage

            chatbot_msg = ChatbotMessage.from_dict(data)

            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            elif chatbot_msg.extensions.get("content", {}).get("recognition"):
                content = chatbot_msg.extensions["content"]["recognition"].strip()
            if not content:
                content = data.get("text", {}).get("content", "").strip()

            if not content:
                logger.warning("Received empty or unsupported message type: {}", chatbot_msg.message_type)
                return

            msg_id = chatbot_msg.message_id or ""
            now = time.time()
            if msg_id in self._processed:
                return
            self._processed[msg_id] = now
            self._processed = {k: v for k, v in self._processed.items() if now - v < 300}

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id or "unknown"
            conversation_type = data.get("conversationType")
            conversation_id = data.get("conversationId") or data.get("openConversationId")
            is_group = conversation_type == "2" and conversation_id
            chat_id = f"group:{conversation_id}" if is_group else sender_id

            msg_data = self.build_message(sender_id, chat_id, content, msg_id)
            response = self.send_to_hub(msg_data)

            if response and response.success and response.content:
                self._send_reply(chat_id, sender_id, is_group, response.content)

        except Exception as e:
            logger.error("DingTalk proxy message handler error: {}", e)

    def _send_reply(self, chat_id: str, sender_id: str, is_group: bool, content: str) -> None:
        """Send a text reply via DingTalk API."""
        try:
            import httpx
            token = self._get_access_token()
            if not token:
                return

            if is_group:
                url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
                payload = {
                    "robotCode": self.config.get("client_id", ""),
                    "openConversationId": chat_id,
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"text": content, "title": "Nanobot Reply"}, ensure_ascii=False),
                }
            else:
                url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
                payload = {
                    "robotCode": self.config.get("client_id", ""),
                    "userIds": [sender_id],
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"text": content, "title": "Nanobot Reply"}, ensure_ascii=False),
                }

            headers = {"x-acs-dingtalk-access-token": token}
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    logger.warning("DingTalk reply failed: {} - {}", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("DingTalk reply error: {}", e)

    def _get_access_token(self) -> str | None:
        """Get DingTalk access token."""
        try:
            import httpx
            url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
            data = {
                "appKey": self.config.get("client_id", ""),
                "appSecret": self.config.get("client_secret", ""),
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=data)
                if resp.status_code == 200:
                    return resp.json().get("accessToken")
        except Exception:
            pass
        return None

    def start(self) -> None:
        """Run the DingTalk Stream connection."""
        from dingtalk_stream import CallbackHandler, DingTalkStreamClient, Credential
        from dingtalk_stream.chatbot import ChatbotMessage

        credential = Credential(self.config.get("client_id", ""), self.config.get("client_secret", ""))
        stream_client = DingTalkStreamClient(credential)

        class Handler(CallbackHandler):
            async def process(self, message):
                self.on_message(message.data)
                return 0, "OK"

        stream_client.register_callback_handler(ChatbotMessage.TOPIC, Handler())

        while True:
            try:
                stream_client.start()
            except Exception as e:
                logger.error("DingTalk stream error: {}", e)
                time.sleep(5)


def main() -> None:
    DingTalkProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("DingTalk proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
