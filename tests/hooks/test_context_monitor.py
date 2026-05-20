"""Tests for nanobot.hooks.context_monitor — ContextMonitorHook."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.hooks.context_monitor import ContextMonitorHook


class _FakeContext:
    """Minimal AgentHookContext-like object for testing."""
    def __init__(self, messages: list, workspace: Path | None = None):
        self.messages = messages
        self.workspace = workspace


class TestContextMonitorHook:
    def test_healthy_context_no_file_created(self, tmp_path):
        hook = ContextMonitorHook()
        msgs = [{"role": "user", "content": "short"}]
        ctx = _FakeContext(msgs, workspace=tmp_path)
        hook._check(ctx)
        health_file = tmp_path / ".context_health.md"
        assert not health_file.exists()

    def test_heavy_context_creates_health_file(self, tmp_path):
        hook = ContextMonitorHook()
        heavy_content = "x" * 150_000
        msgs = [{"role": "user", "content": heavy_content}]
        ctx = _FakeContext(msgs, workspace=tmp_path)
        hook._check(ctx)
        health_file = tmp_path / ".context_health.md"
        assert health_file.exists()
        content = health_file.read_text(encoding="utf-8")
        assert "HEAVY" in content or "CRITICAL" in content

    def test_critical_context_marks_urgent(self, tmp_path):
        hook = ContextMonitorHook()
        critical_content = "x" * 250_000
        msgs = [{"role": "user", "content": critical_content}]
        ctx = _FakeContext(msgs, workspace=tmp_path)
        hook._check(ctx)
        health_file = tmp_path / ".context_health.md"
        assert health_file.exists()
        content = health_file.read_text(encoding="utf-8")
        assert "CRITICAL" in content
        assert "IMMEDIATE ACTION" in content

    def test_bloated_messages_listed(self, tmp_path):
        hook = ContextMonitorHook()
        msgs = [
            {"role": "user", "content": "short"},
            {"role": "user", "content": "x" * 60_000},
            {"role": "assistant", "content": "y" * 60_000},
        ]
        ctx = _FakeContext(msgs, workspace=tmp_path)
        hook._check(ctx)
        health_file = tmp_path / ".context_health.md"
        assert health_file.exists()
        content = health_file.read_text(encoding="utf-8")
        assert "msg idx 1" in content
        assert "msg idx 2" in content

    def test_cleans_up_health_file_when_context_recovers(self, tmp_path):
        hook = ContextMonitorHook()
        health_file = tmp_path / ".context_health.md"
        health_file.write_text("old critical report")
        msgs = [{"role": "user", "content": "short and healthy"}]
        ctx = _FakeContext(msgs, workspace=tmp_path)
        hook._check(ctx)
        assert not health_file.exists()

    def test_no_workspace_returns_gracefully(self):
        hook = ContextMonitorHook()
        ctx = _FakeContext([{"role": "user", "content": "hello"}], workspace=None)
        hook._check(ctx)

    def test_resolve_workspace_from_context(self):
        hook = ContextMonitorHook()
        ctx = _FakeContext([], workspace=Path("/custom/ws"))
        assert hook._resolve_workspace(ctx) == Path("/custom/ws")

    def test_resolve_workspace_fallback_to_agents_marker(self, tmp_path):
        hook = ContextMonitorHook()
        (tmp_path / "SOUL.md").write_text("")
        ctx = _FakeContext([], workspace=None)
        with patch("nanobot.hooks.context_monitor.Path.cwd", return_value=tmp_path):
            ws = hook._resolve_workspace(ctx)
        assert ws == tmp_path

    def test_resolve_workspace_fallback_to_home(self):
        hook = ContextMonitorHook()
        ctx = _FakeContext([], workspace=None)
        with (
            patch("nanobot.hooks.context_monitor.Path.cwd", return_value=Path("/no/agents/here")),
            patch("nanobot.hooks.context_monitor.Path.home", return_value=Path("/fake/home")),
            patch("pathlib.Path.exists", return_value=True),
        ):
            ws = hook._resolve_workspace(ctx)
        assert ws is not None

    def test_resolve_workspace_returns_none_when_not_found(self):
        hook = ContextMonitorHook()
        ctx = _FakeContext([], workspace=None)
        with (
            patch("nanobot.hooks.context_monitor.Path.cwd", return_value=Path("/no/agents/here")),
            patch("nanobot.hooks.context_monitor.Path.home", return_value=Path("/fake/home")),
            patch("pathlib.Path.exists", return_value=False),
        ):
            ws = hook._resolve_workspace(ctx)
        assert ws is None

    def test_before_iteration_calls_check(self, tmp_path):
        hook = ContextMonitorHook()
        ctx = _FakeContext([{"role": "user", "content": "hello"}], workspace=tmp_path)
        import asyncio
        asyncio.run(hook.before_iteration(ctx))
        health_file = tmp_path / ".context_health.md"
        assert not health_file.exists()

    def test_before_iteration_swallows_exception(self):
        hook = ContextMonitorHook()
        import asyncio
        asyncio.run(hook.before_iteration(None))
