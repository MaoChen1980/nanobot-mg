"""Tests for ChannelManager (channel initialization, restart notifications)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nanobot.bus.manager import ChannelManager


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.channels = MagicMock()
    return cfg


@pytest.fixture
def bus():
    bus = MagicMock()
    bus.publish_outbound = MagicMock()
    return bus


class TestInitChannels:
    def test_initializes_with_discover_all(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover:
            discover.return_value = {"telegram": {}, "slack": {}}
            tg = MagicMock()
            tg.enabled = True
            sl = MagicMock()
            sl.enabled = False
            type(config.channels).telegram = tg
            type(config.channels).slack = sl
            manager = ChannelManager(config, bus)
            assert manager.config is config
            assert manager.bus is bus

    def test_enabled_channels_property(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover:
            discover.return_value = {"telegram": {}}
            tg = MagicMock()
            tg.enabled = True
            type(config.channels).telegram = tg
            manager = ChannelManager(config, bus)
            assert "telegram" in manager.enabled_channels

    def test_disabled_channel_not_in_enabled(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover:
            discover.return_value = {"slack": {}}
            sl = MagicMock()
            sl.enabled = False
            type(config.channels).slack = sl
            manager = ChannelManager(config, bus)
            assert "slack" not in manager.enabled_channels


class TestValidateAllowFrom:
    def test_validate_allow_from_is_noop(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover:
            discover.return_value = {}
            manager = ChannelManager(config, bus)
            manager._validate_allow_from()


class TestTranscriptionResolution:
    @pytest.fixture
    def manager(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover:
            discover.return_value = {}
            return ChannelManager(config, bus)

    def test_resolve_openai_key(self, manager):
        manager.config.providers.openai.api_key = "sk-123"
        assert manager._resolve_transcription_key("openai") == "sk-123"

    def test_resolve_groq_key(self, manager):
        manager.config.providers.groq.api_key = "gsk-456"
        assert manager._resolve_transcription_key("groq") == "gsk-456"

    def test_resolve_key_attribute_error_returns_empty(self, manager):
        del manager.config.providers.openai
        assert manager._resolve_transcription_key("openai") == ""

    def test_resolve_openai_base(self, manager):
        manager.config.providers.openai.api_base = "https://api.openai.com/v1"
        assert manager._resolve_transcription_base("openai") == "https://api.openai.com/v1"

    def test_resolve_base_empty_fallback(self, manager):
        manager.config.providers.groq.api_base = None
        assert manager._resolve_transcription_base("groq") == ""


class TestRestartNotification:
    def test_no_notice_noop(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover, \
             patch("nanobot.bus.manager.consume_restart_notice_from_env") as notice:
            discover.return_value = {}
            notice.return_value = None
            manager = ChannelManager(config, bus)
            notice.assert_called_once()

    def test_notice_sends_restart_message(self, config, bus):
        with patch("nanobot.proxy.registry.discover_all") as discover, \
             patch("nanobot.bus.manager.consume_restart_notice_from_env") as notice, \
             patch("nanobot.bus.manager.asyncio.create_task") as create_task:
            discover.return_value = {}
            mock_notice = MagicMock()
            mock_notice.channel = "cli"
            mock_notice.chat_id = "direct"
            mock_notice.started_at_raw = "2025-01-01T00:00:00"
            mock_notice.metadata = {}
            notice.return_value = mock_notice
            manager = ChannelManager(config, bus)
            create_task.assert_called_once()
