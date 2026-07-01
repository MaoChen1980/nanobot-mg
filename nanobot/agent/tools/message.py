"""Message tool for sending messages to users."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.bus.events import OutboundMessage
from nanobot.config.paths import get_workspace_path


@tool_parameters(
    build_parameters_schema(
        content=p("string", "The message content to send"),
        channel=p("string",
            "Optional: target channel override. Defaults to current conversation's "
            "channel+bot (e.g. 'feishu:bot_name'). In most cases you don't need to "
            "set this — just send to the current conversation.",
        ),
        chat_id=p("string",
            "Optional: target chat/recipient override. "
            "Defaults to the current conversation chat.",
        ),
        media=p("array",
            "Optional: list of absolute file paths to send as attachments. "
            "Use this to send documents (PDF, DOCX, XLSX, PPTX), images, or any other file to the user. "
            "The framework handles platform-specific upload automatically.",
            items=p("string", "Absolute path to the file on disk"),
        ),
        buttons=p("array",
            "Optional: inline keyboard buttons as list of rows, each row is list of button labels. "
            "Constraints: max 4 rows, max 3 buttons per row, max 20 chars per label. "
            "When user clicks a button, the label text is returned as their reply.",
            items=p("array", "", items=p("string", "Button label")),
        ),
        message_id=p("string",
            "Optional: message ID to reply to (threading). "
            "Defaults to the current conversation message when targeting the same channel and chat.",
        ),
        required=["content"],
    )
)
class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
    ):
        self._send_callback = send_callback
        self._workspace = Path(workspace).expanduser() if workspace is not None else get_workspace_path()
        self._default_channel: ContextVar[str] = ContextVar("message_default_channel", default=default_channel)
        self._default_chat_id: ContextVar[str] = ContextVar("message_default_chat_id", default=default_chat_id)
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "message_default_message_id",
            default=default_message_id,
        )
        self._default_metadata: ContextVar[dict[str, Any]] = ContextVar(
            "message_default_metadata",
            default={},
        )
        self._record_channel_delivery_var: ContextVar[bool] = ContextVar(
            "message_record_channel_delivery",
            default=False,
        )
        self._deferred: list[OutboundMessage] = []
        self._defer_mode: bool = False

    def set_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Set the current message context."""
        self._default_channel.set(channel)
        self._default_chat_id.set(chat_id)
        self._default_message_id.set(message_id)
        self._default_metadata.set(metadata or {})

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def set_defer_mode(self, active: bool) -> None:
        """When active, execute() queues messages instead of sending."""
        self._defer_mode = active

    async def flush_deferred(self) -> None:
        """Send all deferred messages and clear the queue."""
        for msg in self._deferred:
            await self._send_callback(msg)
        self._deferred.clear()

    def clear_deferred(self) -> None:
        """Discard all deferred messages without sending."""
        self._deferred.clear()

    @property
    def has_deferred(self) -> bool:
        return bool(self._deferred)

    def set_record_channel_delivery(self, active: bool):
        """Mark tool-sent messages as proactive channel deliveries."""
        return self._record_channel_delivery_var.set(active)

    def reset_record_channel_delivery(self, token) -> None:
        """Restore previous proactive delivery recording state."""
        self._record_channel_delivery_var.reset(token)

    instruction = "Send messages/files/buttons to the user. Do NOT use exec or plain text replies to send messages."

    name = "message"

    description = (
        "Send a message or results to the user through chat channels. "
        "Supports text, file attachments (media), inline keyboard buttons, "
        "and cross-channel replies. Unlike text output, sending a message "
        "does not end the turn."
    )

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        buttons: list[list[str]] | None = None,
        **kwargs: Any
    ) -> str:
        from nanobot.agent.loop_utils import strip_think
        content = strip_think(content)

        if buttons is not None:
            if not isinstance(buttons, list) or any(
                not isinstance(row, list) or any(not isinstance(label, str) for label in row)
                for row in buttons
            ):
                return "Error: buttons must be a list of list of strings"
        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()
        channel = channel or default_channel
        chat_id = chat_id or default_chat_id
        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels (e.g. Feishu) use it to determine the target
        # conversation via their Reply API, which would route the message
        # to the wrong chat entirely.
        same_target = channel == default_channel and chat_id == default_chat_id
        if same_target:
            message_id = message_id or self._default_message_id.get()
        else:
            message_id = None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        if media:
            resolved = []
            for p in media:
                if p.startswith(("http://", "https://")):
                    resolved.append(p)
                else:
                    if not os.path.isabs(p):
                        return f"Error: media path must be absolute, got: {p}"
                    fp = Path(p)
                    if not fp.exists():
                        return f"Error: media file not found: {fp.as_posix()}"
                    resolved.append(str(fp))
            media = resolved

        metadata = dict(self._default_metadata.get()) if same_target else {}
        if message_id:
            metadata["message_id"] = message_id
        if self._record_channel_delivery_var.get():
            metadata["_record_channel_delivery"] = True

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            buttons=buttons or [],
            metadata=metadata,
        )

        if self._defer_mode:
            self._deferred.append(msg)
            return (
                "```queued\n"
                "[Message QUEUED for end-of-loop delivery — NOT yet sent to the user. "
                "The framework runs a quality assessment (assess_me) at the end of this turn. "
                "If the assessment approves, all queued messages are flushed to the user channel. "
                "If the assessment requests revision, the queued message is DISCARDED (not delivered). "
                "To deliver a revised version, call message() again with the updated content — "
                "the new message will replace this queued one in the assessment queue. "
                "Do NOT call message() repeatedly with the same content expecting different results.]"
                "\n```"
            )

        try:
            await self._send_callback(msg)
            media_info = f" with {len(media)} attachments" if media else ""
            button_info = f" with {sum(len(row) for row in buttons)} button(s)" if buttons else ""
            return f"Message sent to {channel}:{chat_id}{media_info}{button_info}"
        except Exception as e:
            logger.exception("Failed to send message")
            return f"Error sending message: {str(e)}"