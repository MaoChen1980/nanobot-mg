"""Channel manager — simplified: all channels run as proxy subprocesses."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.utils.restart import consume_restart_notice_from_env, format_restart_completed_message

# Retry delays for message sending (exponential backoff: 1s, 2s, 4s)
_SEND_RETRY_DELAYS = (1, 2, 4)


class ChannelManager:
    """Tracks enabled channels and handles restart notifications.

    All channels run as proxy subprocesses managed by ProxyManager.
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._dispatch_task: asyncio.Task | None = None

        self._init_channels()
        self._notify_restart_done_if_needed()

    def _init_channels(self) -> None:
        """Log which channels are enabled (all run as proxy processes)."""
        from nanobot.proxy.registry import discover_all

        for name, info in discover_all().items():
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue
            enabled = (
                section.get("enabled", False)
                if isinstance(section, dict)
                else getattr(section, "enabled", False)
            )
            if not enabled:
                continue
            bots = section.get("bots", []) if isinstance(section, dict) else []
            if bots:
                logger.debug(
                    "Channel {}: multi-bot config ({} bots) — runs via proxy",
                    name, len(bots),
                )
            else:
                logger.info(
                    "Channel {}: enabled but no 'bots' list — configure a 'bots' array.",
                    name,
                )

        self._validate_allow_from()
        logger.info("ChannelManager initialized (all channels run as proxy processes)")

    def _resolve_transcription_key(self, provider: str) -> str:
        """Pick the API key for the configured transcription provider."""
        try:
            if provider == "openai":
                return self.config.providers.openai.api_key
            return self.config.providers.groq.api_key
        except AttributeError:
            return ""

    def _resolve_transcription_base(self, provider: str) -> str:
        """Pick the API base URL for the configured transcription provider."""
        try:
            if provider == "openai":
                return self.config.providers.openai.api_base or ""
            return self.config.providers.groq.api_base or ""
        except AttributeError:
            return ""

    def _validate_allow_from(self) -> None:
        """Check allow_from config — no in-process channels to validate currently."""
        pass

    def _notify_restart_done_if_needed(self) -> None:
        """Send restart completion message when runtime env markers are present."""
        notice = consume_restart_notice_from_env()
        if not notice:
            return
        if not self.config or not self.bus:
            return
        from nanobot.bus.events import OutboundMessage

        self._send_restart_message(notice, OutboundMessage)

    def _send_restart_message(self, notice: Any, outbound_cls: type) -> None:
        """Publish restart-completion notice to the bus."""
        asyncio.create_task(self._publish_restart_delivery(
            outbound_cls(
                channel=notice.channel,
                chat_id=notice.chat_id,
                content=format_restart_completed_message(notice.started_at_raw),
                metadata=dict(notice.metadata or {}),
            ),
        ))

    async def _publish_restart_delivery(self, msg: Any) -> None:
        """Publish a message to the outbound bus."""
        await self.bus.publish_outbound(msg)

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        from nanobot.proxy.registry import discover_all
        result = []
        for name in discover_all():
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue
            enabled = section.get("enabled", False) if isinstance(section, dict) else getattr(section, "enabled", False)
            if enabled:
                result.append(name)
        return result
