"""Message protocol for proxy <-> hub communication."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProxyMessage:
    """Inbound message from proxy to hub."""
    channel: str          # e.g. "feishu"
    bot: str              # e.g. "nanobot"
    sender_id: str        # e.g. "ou_xxx"
    chat_id: str          # e.g. "oc_xxx"
    content: str
    message_id: str      # Platform message ID, used for dedup and reply threading
    media: list[str] = field(default_factory=list)
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "bot": self.bot,
            "sender_id": self.sender_id,
            "chat_id": self.chat_id,
            "content": self.content,
            "message_id": self.message_id,
            "media": self.media,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def to_inbound_message(self) -> "InboundMessage":
        """Convert to bus InboundMessage for the agent loop."""
        from nanobot.bus.events import InboundMessage
        from datetime import datetime, timezone
        return InboundMessage(
            channel=f"proxy:{self.channel}:{self.bot}",
            sender_id=self.sender_id,
            chat_id=self.chat_id,
            content=self.content,
            timestamp=datetime.fromisoformat(self.timestamp) if self.timestamp else datetime.now(timezone.utc),
            media=self.media,
            metadata=self.metadata,
            # Match hub's session key format (channel:bot:sender_id) so the bus
            # dispatch path uses the same lock key as the hub path, preventing
            # concurrent processing of the same user's messages.
            session_key_override=f"{self.channel}:{self.bot}:{self.sender_id}",
        )

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyMessage":
        metadata = d.get("metadata", {})
        if not isinstance(metadata, dict):
            logger.warning("ProxyMessage metadata is not a dict: type={}, using empty dict", type(metadata).__name__)
            metadata = {}
        content = d.get("content", "")
        if isinstance(content, list):
            # Join list elements with newlines (e.g., multi-part messages)
            content = "\n".join(str(c) for c in content)
        elif not isinstance(content, str):
            logger.warning("ProxyMessage content is not a str: type={}, converting to str", type(content).__name__)
            content = str(content)
        return cls(
            channel=d["channel"],
            bot=d["bot"],
            sender_id=d["sender_id"],
            chat_id=d["chat_id"],
            content=content,
            message_id=d["message_id"],
            media=d.get("media", []),
            timestamp=d.get("timestamp", ""),
            metadata=metadata,
        )


@dataclass
class HubResponse:
    """Response from hub to proxy."""
    success: bool
    reply_to: str = ""           # message_id to reply to
    content: str = ""            # text reply
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    buttons: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "reply_to": self.reply_to,
            "content": self.content,
            "media": self.media,
            "metadata": self.metadata,
            "error": self.error,
        }
        if self.buttons:
            d["buttons"] = self.buttons
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HubResponse":
        return cls(
            success=d.get("success", False),
            reply_to=d.get("reply_to", ""),
            content=d.get("content", ""),
            media=d.get("media", []),
            metadata=d.get("metadata", {}),
            error=d.get("error", ""),
        )


def outbound_to_hub_response(msg, reply_to: str = "") -> HubResponse:
    """Convert an OutboundMessage to HubResponse for proxy wire transport.
    Lives in proxy/protocol.py (the proxy layer) rather than on OutboundMessage
    itself to avoid a reverse dependency from bus -> proxy.
    """
    return HubResponse(
        success=True,
        reply_to=reply_to,
        content=msg.content,
        media=getattr(msg, "media", []),
        metadata=getattr(msg, "metadata", {}),
        buttons=getattr(msg, "buttons", []),
    )


