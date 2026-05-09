"""Feishu proxy - runs as a separate process, connects to Feishu WebSocket and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import re
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
    # Push delivery from Hub (cron reminders, etc.)
    # ------------------------------------------------------------------

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Handle a push delivery from hub — send as a new message to the chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._send_text_reply(chat_id, None, content)

    # ------------------------------------------------------------------
    # Reply / reaction helpers
    # ------------------------------------------------------------------

    # ── Content detection ──────────────────────────────────────────────

    @staticmethod
    def _has_rich_content(text: str) -> bool:
        """Detect content that benefits from interactive card rendering.

        Checks for code blocks (```` ``` ````) and markdown tables (``|...|``
        followed by a separator line ``|---|``), which Feishu post messages
        and legacy ``lark_md`` tags cannot render properly.
        """
        if "```" in text:
            return True
        return bool(re.search(r'\|.+\|\r?\n\|[-:| ]+\|', text))

    @staticmethod
    def _extract_header(content: str) -> tuple[str | None, str]:
        """Extract first level-1 heading as a card header title.

        Looks for ``# Title`` among the first few non-empty lines. When found,
        the heading line is removed from the body content so it doesn't
        render twice — once in the header bar and once in the body.

        Returns ``(header_title, remaining_content)``.
        """
        lines = content.split("\n")
        for i, line in enumerate(lines[:10]):
            stripped = line.strip()
            if stripped:
                m = re.match(r"^#\s+(.+)$", stripped)
                if m:
                    body = "\n".join(lines[i + 1 :]).strip()
                    return m.group(1), body
                break
        return None, content

    # ── Table fallback for non-card paths ──────────────────────────────

    @staticmethod
    def _wrap_tables_in_code_fences(content: str) -> str:
        """Wrap markdown tables in code fences for compatibility with non-card message types.

        ``tag: "md"`` in post messages and ``lark_md`` in v1 cards cannot render
        pipe-delimited tables, so wrapping them in ``` fences preserves layout.
        """
        lines = content.split("\n")
        result: list[str] = []
        table_lines: list[str] = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            is_table = stripped.startswith("|") and stripped.endswith("|")

            if is_table:
                if not in_table:
                    in_table = True
                    table_lines = [line]
                else:
                    table_lines.append(line)
            else:
                if in_table:
                    if len(table_lines) > 2:
                        result.append("```")
                        result.extend(table_lines)
                        result.append("```")
                    else:
                        result.extend(table_lines)
                    in_table = False
                    table_lines = []
                result.append(line)

        if in_table:
            if len(table_lines) > 2:
                result.append("```")
                result.extend(table_lines)
                result.append("```")
            else:
                result.extend(table_lines)

        return "\n".join(result)

    # ── Send strategies ────────────────────────────────────────────────

    def _send_card_reply(self, chat_id: str, content: str) -> bool:
        """Send as Feishu interactive card v2.0 with native markdown.

        Supports the full markdown spec including tables, code blocks,
        headings, lists, and inline formatting. Caller should fall back to
        :meth:`_send_post_reply` or :meth:`_send_plain_text` on failure.
        """
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            header_text, body = self._extract_header(content)
            elements: list[dict[str, Any]] = [
                {"tag": "markdown", "content": body or content},
            ]

            card: dict[str, Any] = {
                "schema": "2.0",
                "config": {"width_mode": "fill"},
                "body": {"elements": elements},
            }
            if header_text:
                template = self.config.get("cardTemplate", "blue")
                card["header"] = {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": template,
                }
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(json.dumps(card))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if resp.success():
                return True
            logger.warning("Card send failed ({}): {} - will fall back", resp.code, resp.msg)
        except Exception as e:
            logger.error("Card send exception: {}", e)
        return False

    def _send_post_reply(self, chat_id: str, content: str) -> bool:
        """Send as post message with a markdown body.

        Lighter than interactive cards — good for simple text without
        tables or code blocks. ``tag: "md"`` supports bold, italic,
        inline code, links, and lists.
        """
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            payload = {
                "zh_cn": {
                    "content": [
                        [{"tag": "md", "text": content}],
                    ],
                },
            }
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("post")
                    .content(json.dumps(payload))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if resp.success():
                return True
            logger.warning("Post send failed ({}): {} - will fall back", resp.code, resp.msg)
        except Exception as e:
            logger.error("Post send exception: {}", e)
        return False

    def _send_plain_text(self, chat_id: str, content: str) -> None:
        """Last-resort fallback: send as plain text with no formatting."""
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
            self._client.im.v1.message.create(request)
        except Exception as e:
            logger.error("Plain-text fallback failed: {}", e)

    # ── Public send ────────────────────────────────────────────────────

    def _send_text_reply(self, chat_id: str, root_id: str | None, content: str) -> None:
        """Send a reply with automatic format selection based on content and config.

        Routing logic (config key ``renderMode``):
          * ``card`` (default) — always use interactive card v2.0 (native markdown with tables)
          * ``raw`` — use post message (lightweight, tables → code fences)
          * ``auto`` — detect rich content (code blocks, tables) → card; else post

        Falls back through the chain: card → post → plain text.
        """
        render_mode = self.config.get("renderMode", "card")
        use_card = render_mode == "card" or (
            render_mode == "auto" and self._has_rich_content(content)
        )

        if use_card:
            if self._send_card_reply(chat_id, content):
                return

        processed = self._wrap_tables_in_code_fences(content)
        if self._send_post_reply(chat_id, processed):
            return

        self._send_plain_text(chat_id, processed)

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
        except Exception as e:
            logger.debug("Failed to remove reaction: {}", e)

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
