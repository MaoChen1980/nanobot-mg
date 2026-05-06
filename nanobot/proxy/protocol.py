"""Message protocol for proxy <-> hub communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProxyMessage:
    """Inbound message from proxy to hub."""
    channel: str          # e.g. "feishu"
    bot: str              # e.g. "nanobot"
    sender_id: str        # e.g. "ou_xxx"
    chat_id: str          # e.g. "oc_xxx"
    content: str
    message_id: str      # 飞书 message_id，用于去重和回调
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
        from datetime import datetime
        return InboundMessage(
            channel=f"proxy:{self.channel}:{self.bot}",
            sender_id=self.sender_id,
            chat_id=self.chat_id,
            content=self.content,
            timestamp=datetime.fromisoformat(self.timestamp) if self.timestamp else datetime.now(),
            media=self.media,
            metadata=self.metadata,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyMessage":
        return cls(
            channel=d["channel"],
            bot=d["bot"],
            sender_id=d["sender_id"],
            chat_id=d["chat_id"],
            content=d["content"],
            message_id=d["message_id"],
            media=d.get("media", []),
            timestamp=d.get("timestamp", ""),
            metadata=d.get("metadata", {}),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reply_to": self.reply_to,
            "content": self.content,
            "media": self.media,
            "metadata": self.metadata,
            "error": self.error,
        }

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


