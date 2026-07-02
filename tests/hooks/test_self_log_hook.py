"""Tests for SelfLogHook (per-iteration metrics capture)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from nanobot.hooks.self_log_hook import SelfLogHook


@pytest.fixture
def hook(tmp_path):
    h = SelfLogHook()
    h.LOG_FILE = tmp_path / "self_review_log.jsonl"
    return h


class FakeContext:
    def __init__(self, **kwargs):
        self.tool_calls = kwargs.get("tool_calls", [])
        self.tool_results = kwargs.get("tool_results", [])
        self.tool_events = kwargs.get("tool_events", [])  # {"name", "status", "detail", "duration_ms"}
        self.usage = kwargs.get("usage", {})
        self.iteration = kwargs.get("iteration", 1)
        self.error = kwargs.get("error")
        self.final_content = kwargs.get("final_content")
        self.messages = kwargs.get("messages", [])


class TestCapture:
    def test_capture_with_tool_calls_and_results(self, hook):
        tc1 = MagicMock()
        tc1.name = "web_search"
        tc2 = MagicMock()
        tc2.name = "read_file"
        ctx = FakeContext(
            tool_calls=[tc1, tc2],
            tool_results=["result"],
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            iteration=1,
        )
        hook._capture(ctx)
        assert hook.LOG_FILE.exists()
        lines = hook.LOG_FILE.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool_count"] == 2
        assert entry["total_tokens"] == 150

    def test_capture_without_usage(self, hook):
        ctx = FakeContext(tool_calls=[], tool_results=[], usage={}, iteration=1)
        hook._capture(ctx)
        entry = json.loads(hook.LOG_FILE.read_text())
        assert entry["tool_count"] == 0
        assert entry["total_tokens"] == 0

    def test_capture_detects_error(self, hook):
        ctx = FakeContext(tool_calls=[], tool_results=[], usage={}, iteration=1, error="timeout")
        hook._capture(ctx)
        entry = json.loads(hook.LOG_FILE.read_text())
        assert entry["has_error"] is True

    def test_capture_detects_final_content(self, hook):
        ctx = FakeContext(tool_calls=[], tool_results=[], usage={}, iteration=1, final_content="ok")
        hook._capture(ctx)
        entry = json.loads(hook.LOG_FILE.read_text())
        assert entry["has_final_content"] is True

    def test_capture_duration_from_tool_events(self, hook):
        """duration_ms lives in tool_events, not tool_results."""
        tc = MagicMock()
        tc.name = "exec"
        ctx = FakeContext(
            tool_calls=[tc],
            tool_results=["ok"],
            tool_events=[
                {"name": "exec", "status": "ok", "detail": "ok", "duration_ms": 1500},
            ],
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            iteration=1,
        )
        hook._capture(ctx)
        entry = json.loads(hook.LOG_FILE.read_text())
        assert entry["duration_sec"] == 1.5

    def test_capture_discomfort_signals_with_tool_name(self, hook):
        """discomfort_signals now returns {pattern, tool} dicts, not plain strings."""
        tc = MagicMock()
        tc.name = "read_file"
        ctx = FakeContext(
            tool_calls=[tc],
            tool_results=[{"status": "fail", "result": "Error: file not found", "error": "missing"}],
            tool_events=[],
            usage={},
            iteration=1,
        )
        hook._capture(ctx)
        entry = json.loads(hook.LOG_FILE.read_text())
        assert entry["discomfort_signals"] == [{"pattern": "error", "tool": "read_file"}]
        assert entry["error_count"] == 1


class TestPredicates:
    def test_is_error_result(self):
        hook = SelfLogHook()
        # Structured fail status counts as error
        assert hook._is_error_result({"status": "fail", "error": "boom"})
        # Non-string passed (legacy code path) — must NOT match on substring
        assert not hook._is_error_result("Error: connection failed")
        assert not hook._is_error_result("success")

    def test_is_empty_result(self):
        hook = SelfLogHook()
        assert hook._is_empty_result(None)
        assert hook._is_empty_result("")
        assert hook._is_empty_result("None")
        assert hook._is_empty_result("[]")
        assert not hook._is_empty_result("data")
        # Structured: empty result field counts as empty
        assert hook._is_empty_result({"status": "ok", "result": ""})

    def test_detect_discomfort(self):
        hook = SelfLogHook()
        # Only actual failures count now
        assert hook._detect_discomfort({"status": "fail", "result": "not found"})
        assert hook._detect_discomfort({"status": "fail", "result": "permission denied"})
        assert hook._detect_discomfort({"status": "fail", "result": "timeout"})
        # Successful messages mentioning error keywords are NOT discomfort
        assert hook._detect_discomfort({"status": "ok", "result": "0 errors found"}) is None
        assert hook._detect_discomfort({"status": "ok", "result": "completed within timeout"}) is None
        # Plain strings (legacy path) no longer auto-detect
        assert hook._detect_discomfort("not found") is None


@pytest.mark.asyncio
async def test_after_iteration_calls_capture(hook):
    ctx = FakeContext(tool_calls=[], tool_results=[], usage={}, iteration=1)
    await hook.after_iteration(ctx)
    assert hook.LOG_FILE.exists()


@pytest.mark.asyncio
async def test_after_iteration_swallows_exception(hook):
    class BadContext:
        tool_calls = None
        tool_results = None
        usage = None
        iteration = "not-an-int"

    await hook.after_iteration(BadContext())
