"""Feishu proxy - runs as a separate process, connects to Feishu WebSocket and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import threading
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel
from nanobot.proxy.protocol import HubResponse, ProxyMessage


class FeishuProxyChannel(BaseProxyChannel):
    """Feishu message events forwarded to Hub via TCP."""

    CHANNEL_NAME = "Feishu"
    REQUIRED_CONFIG_FIELDS = ["appId", "appSecret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._client: Any = None  # lark_oapi Client, set in start()
        self._reaction_emoji = config.get("react_emoji", "THUMBSUP")
        self._done_emoji = config.get("done_emoji")
        self._domain = (
            "https://open.feishu.cn"
            if config.get("domain", "feishu") == "feishu"
            else "https://open.larksuite.com"
        )

    # ------------------------------------------------------------------
    # Message handler (called from Feishu SDK thread)
    # ------------------------------------------------------------------

    def on_message(self, data: Any) -> None:
        """Sync callback from Feishu SDK - forward message to Hub."""
        logger.info("Feishu WS on_message called: data={}", type(data).__name__)
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = getattr(message, "message_id", None)
            if not message_id or self.check_duplicate(message_id):
                return

            content = getattr(message, "content", "")
            try:
                content_obj = json.loads(content)
                text = content_obj.get("text", "") if isinstance(content_obj, dict) else str(content_obj)
            except Exception:
                text = content

            sender_id_obj = getattr(sender, "sender_id", None)
            if sender_id_obj is not None and hasattr(sender_id_obj, "open_id"):
                sender_id = sender_id_obj.open_id
            else:
                sender_id = str(sender_id_obj or "")
            chat_id = getattr(message, "chat_id", "")

            # THUMBSUP reaction immediately
            self._add_reaction(message_id, self._reaction_emoji)

            # Forward to Hub
            def _do_reply(response: HubResponse | None) -> None:
                try:
                    if response and response.success and response.content:
                        self._send_text_reply(chat_id, message_id, response.content)
                    if response and response.success and response.metadata.get("done_emoji"):
                        self._add_reaction(message_id, response.metadata["done_emoji"])
                    elif response:
                        self._add_reaction(message_id, self._done_emoji)
                    self._remove_reaction(message_id)
                except Exception as e:
                    logger.error("Failed to send reply/reaction: {}", e)

            msg_data = self.build_message(sender_id, chat_id, text, message_id)
            t = threading.Thread(
                target=lambda: _do_reply(self.send_to_hub(msg_data)),
                daemon=True,
            )
            t.start()

        except Exception as e:
            logger.error("Feishu proxy message handler error: {}", e)

    def on_reaction(self, data: Any) -> None:
        """Handle reaction events (im.message.reaction.created_v1)."""
        pass

    # ------------------------------------------------------------------
    # Reply / reaction helpers
    # ------------------------------------------------------------------

    def _send_text_reply(self, chat_id: str, root_id: str | None, content: str) -> None:
        """Send a text reply to the chat."""
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": content}))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if not resp.success():
                logger.warning("Failed to send reply: {} - {}", resp.code, resp.msg)
        except Exception as e:
            logger.error("Failed to send reply: {}", e)

    def _add_reaction(self, message_id: str, emoji: str) -> None:
        """Add reaction emoji to message."""
        if not emoji:
            return
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                Emoji,
            )
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji).build())
                    .build()
                )
                .build()
            )
            self._client.im.v1.message_reaction.create(request)
        except Exception as e:
            logger.debug("Failed to add reaction: {}", e)

    def _remove_reaction(self, message_id: str) -> None:
        """Remove reactions from message (best-effort)."""
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .build()
            )
            self._client.im.v1.message_reaction.delete(request)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Set up Feishu WebSocket client and enter the event loop."""
        import lark_oapi as lark

        self._client = (
            lark.Client.builder()
            .app_id(self.config["appId"])
            .app_secret(self.config["appSecret"])
            .domain(self._domain)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        builder = (
            lark.EventDispatcherHandler.builder(
                self.config.get("encryptKey", "") or "",
                self.config.get("verificationToken", "") or "",
            )
            .register_p2_im_message_receive_v1(self.on_message)
            .register_p2_im_message_reaction_created_v1(self.on_reaction)
        )
        event_handler = builder.build()

        ws_client = lark.ws.Client(
            self.config["appId"],
            self.config["appSecret"],
            domain=self._domain,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def run_ws() -> None:
            import lark_oapi.ws as _lark_ws

            logger.info("Feishu WS loop starting, connecting to {}...", self._domain)
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws.client.loop = ws_loop
            try:
                ws_client.start()
                logger.info("Feishu WS: client.start() returned (should not happen)")
            except Exception as e:
                logger.error("Feishu WS error: {}", e)
            finally:
                ws_loop.close()
                logger.info("Feishu WS loop ended")

        thread = threading.Thread(target=run_ws, daemon=True)
        thread.start()

        while True:
            time.sleep(5)


def main() -> None:
    FeishuProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Feishu proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
