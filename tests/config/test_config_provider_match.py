"""Tests for Config provider matching logic (_match_provider, get_api_key, etc.)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nanobot.config.schema import Config, ExtractorConfig


def _make_cfg():
    cfg = MagicMock()
    cfg.agents = MagicMock()
    cfg.agents.defaults = MagicMock()
    cfg.agents.defaults.provider = "auto"
    cfg.agents.defaults.model = "minimax/MiniMax-M2.7"
    cfg.providers = MagicMock()
    # Wire real _match_provider method
    cfg._match_provider = lambda model=None: Config._match_provider(cfg, model)
    cfg.get_provider = lambda model=None: Config.get_provider(cfg, model)
    cfg.get_provider_name = lambda model=None: Config.get_provider_name(cfg, model)
    cfg.get_api_key = lambda model=None: Config.get_api_key(cfg, model)
    return cfg


class TestMatchProvider:
    def test_forced_provider_lookup(self):
        cfg = _make_cfg()
        cfg.agents.defaults.provider = "deepseek"
        spec = MagicMock()
        spec.name = "deepseek"
        spec.keywords = ["deepseek"]
        spec.is_oauth = False
        spec.is_local = False
        spec.detect_by_base_keyword = None
        with patch("nanobot.providers.registry.find_by_name") as find:
            find.return_value = spec
            p = MagicMock()
            p.api_key = "sk-123"
            cfg.providers.deepseek = p
            result, name = cfg._match_provider()
            assert name == "deepseek"
            find.assert_called_once_with("deepseek")

    def test_forced_provider_not_found(self):
        cfg = _make_cfg()
        cfg.agents.defaults.provider = "unknown"
        with patch("nanobot.providers.registry.find_by_name") as find:
            find.return_value = None
            result, name = cfg._match_provider()
            assert name is None

    def test_explicit_prefix_match(self):
        cfg = _make_cfg()
        cfg.agents.defaults.model = "copilot/codex"
        copilot_spec = MagicMock()
        copilot_spec.name = "copilot"
        copilot_spec.keywords = ["copilot"]
        copilot_spec.is_oauth = False
        copilot_spec.is_local = False
        copilot_spec.detect_by_base_keyword = None
        with patch("nanobot.providers.registry.PROVIDERS", [copilot_spec]):
            p = MagicMock()
            p.api_key = "gh-123"
            cfg.providers.copilot = p
            result, name = cfg._match_provider(model="copilot/codex")
            assert name == "copilot"

    def test_keyword_matches(self):
        cfg = _make_cfg()
        spec = MagicMock()
        spec.name = "deepseek"
        spec.keywords = ["deepseek"]
        spec.is_oauth = False
        spec.is_local = False
        spec.detect_by_base_keyword = None
        with patch("nanobot.providers.registry.PROVIDERS", [spec]):
            p = MagicMock()
            p.api_key = "sk-789"
            cfg.providers.deepseek = p
            result, name = cfg._match_provider(model="deepseek-chat")
            assert name == "deepseek"
            assert result is p

    def test_no_match_returns_none(self):
        cfg = _make_cfg()
        spec = MagicMock()
        spec.name = "deepseek"
        spec.keywords = ["deepseek"]
        spec.is_oauth = False
        spec.is_local = False
        spec.detect_by_base_keyword = None
        # Final fallback iterates configured providers with api_key — ensure none match
        cfg.providers.deepseek.api_key = None
        with patch("nanobot.providers.registry.PROVIDERS", [spec]):
            result, name = cfg._match_provider(model="completely-unknown")
            assert name is None

    def test_get_provider(self):
        cfg = _make_cfg()
        cfg._match_provider = lambda model=None: (MagicMock(), "test")
        assert cfg.get_provider() is not None

    def test_get_provider_name(self):
        cfg = _make_cfg()
        cfg._match_provider = lambda model=None: (MagicMock(), "test")
        assert cfg.get_provider_name() == "test"

    def test_get_api_key(self):
        cfg = _make_cfg()
        p = MagicMock()
        p.api_key = "sk-abc"
        cfg.get_provider = lambda model=None: p
        assert cfg.get_api_key() == "sk-abc"

    def test_get_api_key_none(self):
        cfg = _make_cfg()
        cfg.get_provider = lambda model=None: None
        assert cfg.get_api_key() is None


class TestDetectTimezone:
    def test_returns_string(self):
        from nanobot.config.schema import _detect_timezone
        result = _detect_timezone()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_windows_fallback_via_winreg(self):
        import nanobot.config.schema as schema
        original = schema._WINDOWS_TO_IANA.get
        # If winreg import fails on non-Windows, it falls back to UTC
        result = schema._detect_timezone()
        assert isinstance(result, str)


class TestExtractorConfig:
    def test_build_schedule_with_cron(self):
        ec = ExtractorConfig(cron="0 */2 * * *")
        sched = ec.build_schedule("UTC")
        assert sched.kind == "cron"
        assert sched.expr == "0 */2 * * *"

    def test_build_schedule_with_interval(self):
        ec = ExtractorConfig(interval_h=1.0, cron=None)
        sched = ec.build_schedule("UTC")
        assert sched.kind == "every"

    def test_describe_schedule_with_cron(self):
        ec = ExtractorConfig(cron="0 */2 * * *")
        assert "cron" in ec.describe_schedule()

    def test_describe_schedule_with_interval(self):
        ec = ExtractorConfig(interval_h=1.5, cron=None)
        assert "1.5" in ec.describe_schedule()
