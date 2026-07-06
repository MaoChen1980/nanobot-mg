"""File sending tool for sending files to users."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.bus.events import OutboundMessage
from nanobot.config.paths import get_workspace_path


@tool_parameters(
    build_parameters_schema(
        media=p("array",
            "List of absolute file paths (or URLs) to send as attachments. "
            "Supports documents (PDF, DOCX, XLSX, PPTX), images, audio, video, and any other file type. "
            "The framework handles platform-specific upload automatically.",
            items=p("string", "Absolute path to the file on disk, or http(s) URL"),
        ),
        content=p("string",
            "Optional: caption or description text for the files being sent.",
        ),
        channel=p("string",
            "Optional: target channel override. Defaults to current conversation's "
            "channel+bot (e.g. 'feishu:bot_name').",
        ),
        chat_id=p("string",
            "Optional: target chat/recipient override. "
            "Defaults to the current conversation chat.",
        ),
        required=["media"],
    )
)
class FileTool(Tool):
    """Tool to send files/documents/images to users on chat channels."""

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
        self._default_channel: ContextVar[str] = ContextVar("file_default_channel", default=default_channel)
        self._default_chat_id: ContextVar[str] = ContextVar("file_default_chat_id", default=default_chat_id)
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "file_default_message_id",
            default=default_message_id,
        )
        self._default_metadata: ContextVar[dict[str, Any]] = ContextVar(
            "file_default_metadata",
            default={},
        )
        self._record_channel_delivery_var: ContextVar[bool] = ContextVar(
            "file_record_channel_delivery",
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

    def set_record_channel_delivery(self, active: bool):
        """Mark tool-sent files as proactive channel deliveries."""
        return self._record_channel_delivery_var.set(active)

    def reset_record_channel_delivery(self, token) -> None:
        """Restore previous proactive delivery recording state."""
        self._record_channel_delivery_var.reset(token)

    instruction = (
        "Send files/documents/images to the user as attachments. "
        "Use this tool when you have files to deliver — reports, screenshots, generated documents, etc. "
        "The files are sent as attachments, not inline. "
        "If you also need to send text, include it in the optional content parameter as a caption."
    )

    name = "send_file"

    description = (
        "Send files/documents/images/audio/video to the user through chat channels. "
        "Accepts absolute file paths or URLs. The framework handles platform-specific upload. "
        "Unlike text output, sending files does not end the turn."
    )

    async def execute(
        self,
        media: list[str],
        content: str = "",
        channel: str | None = None,
        chat_id: str | None = None,
        **kwargs: Any
    ) -> str:
        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()
        channel = channel or default_channel
        chat_id = chat_id or default_chat_id
        same_target = channel == default_channel and chat_id == default_chat_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: File sending not configured"

        if not media:
            return "Error: media list is empty"

        resolved: list[str] = []
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

        metadata = dict(self._default_metadata.get()) if same_target else {}
        if self._record_channel_delivery_var.get():
            metadata["_record_channel_delivery"] = True

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=resolved,
            buttons=[],
            metadata=metadata,
        )

        try:
            await self._send_callback(msg)
            return f"{len(resolved)} file(s) sent to {channel}:{chat_id}"
        except Exception as e:
            logger.exception("Failed to send files")
            return f"Error sending files: {str(e)}"
