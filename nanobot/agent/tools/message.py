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
            "Optional: target channel override (e.g. 'slack', 'feishu'). "
            "Defaults to the current conversation channel.",
        ),
        chat_id=p("string",
            "Optional: target chat/recipient override. "
            "Defaults to the current conversation chat.",
        ),
        media=p("array",
            "Optional: list of absolute file paths to attach. Supports images, video, audio, documents.",
            items=p("string", "Absolute path to a file"),
        ),
        buttons=p("array",
            "Optional: inline keyboard buttons as list of rows, each row is list of button labels. "
            "Constraints: max 4 rows, max 3 buttons per row, max 20 chars per label. "
            "When user clicks a button, the label text is returned as their reply.",
            items=p("array", "", items=p("string", "Button label")),
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
        self._sent_in_turn_var: ContextVar[bool] = ContextVar("message_sent_in_turn", default=False)
        self._record_channel_delivery_var: ContextVar[bool] = ContextVar(
            "message_record_channel_delivery",
            default=False,
        )

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

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False

    def set_record_channel_delivery(self, active: bool):
        """Mark tool-sent messages as proactive channel deliveries."""
        return self._record_channel_delivery_var.set(active)

    def reset_record_channel_delivery(self, token) -> None:
        """Restore previous proactive delivery recording state."""
        self._record_channel_delivery_var.reset(token)

    @property
    def _sent_in_turn(self) -> bool:
        return self._sent_in_turn_var.get()

    @_sent_in_turn.setter
    def _sent_in_turn(self, value: bool) -> None:
        self._sent_in_turn_var.set(value)

    name = "message_tool"

    description = (
        "**Purpose**: Send a message to the user, then continue working. "
        "Unlike natural text output (which ends the turn and waits for user reply), "
        "this tool delivers the message immediately while the agent loop continues.\n\n"
        "**When to use**:\n"
        "- You need to send a message without stopping the agent loop\n"
        "**When NOT to use**:\n"
        "- If you're done working and just want to reply — use natural text output instead\n"
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

        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            button_info = f" with {sum(len(row) for row in buttons)} button(s)" if buttons else ""
            return f"Message sent to {channel}:{chat_id}{media_info}{button_info}"
        except Exception as e:
            logger.exception("Failed to send message")
            return f"Error sending message: {str(e)}"