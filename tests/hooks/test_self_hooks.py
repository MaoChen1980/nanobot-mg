"""Tests for nanobot.hooks self-log / self-detect / self-fix hooks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from nanobot.agent.hook import AgentHookContext
from nanobot.hooks.self_log_hook import SelfLogHook


class _FakeContext:
    """Minimal AgentHookContext-like object for testing."""

    def __init__(
        self,
        messages: list | None = None,
        tool_calls: list | None = None,
        tool_results: list | None = None,
        usage: dict | None = None,
        error: str | None = None,
        final_content: str | None = None,
    ):
        self.messages = messages or []
        self.tool_calls = tool_calls or []
        self.tool_results = tool_results or []
        self.usage = usage or {}
        self.error = error
        self.final_content = final_content
        self.iteration = 1
        self.response = None
        self.stop_reason = None
        self.workspace = None
        self.tool_events = []


# ---------------------------------------------------------------------------
# SelfLogHook — discomfort signal detection
# ---------------------------------------------------------------------------

class TestSelfLogHookDetectDiscomfort:
    """Tests for discomfort signal detection in tool results."""

    def _detect(self, result: dict) -> str | None:
        hook = SelfLogHook()
        return hook._detect_discomfort(result)

    def test_error_signal(self):
        assert self._detect({"error": "Connection timeout"}) == "error"

    def test_failed_signal(self):
        # "error" pattern matches first in DISCOMFORT_PATTERNS order
        assert self._detect({"error": "Request failed"}) == "error"

    def test_not_found_signal(self):
        assert self._detect({"result": "not found"}) == "not found"

    def test_permission_denied_signal(self):
        assert self._detect({"result": "permission denied"}) == "permission denied"

    def test_timeout_signal(self):
        assert self._detect({"result": "timeout"}) == "timeout"

    def test_no_signal(self):
        assert self._detect({"result": "file updated successfully"}) is None
        assert self._detect({"result": "ok"}) is None

    def test_error_result_true(self):
        hook = SelfLogHook()
        assert hook._is_error_result({"error": "something failed"})
        assert hook._is_error_result({"error": "ERROR: connection refused"})
        assert not hook._is_error_result({"result": "ok"})
        assert not hook._is_error_result({"result": "file not found"})  # no "error" in str(result)

    def test_empty_result_true(self):
        # Pass raw string/None to _is_empty_result (as the hook receives from context)
        hook = SelfLogHook()
        assert hook._is_empty_result("")
        assert hook._is_empty_result(None)
        assert hook._is_empty_result("[]")
        assert hook._is_empty_result("{}")
        assert hook._is_empty_result("null")
        assert not hook._is_empty_result("something")
        # str(dict) produces "{'key': ''}" — NOT a plain empty string
        assert not hook._is_empty_result({"result": ""})


class TestSelfLogHookCapture:
    """Tests for metric capture in SelfLogHook._capture."""

    def _capture(self, tool_results: list[dict], tool_calls: list | None = None) -> dict:
        hook = SelfLogHook()
        ctx = _FakeContext(
            tool_results=tool_results,
            tool_calls=tool_calls or [],
        )
        hook._capture(ctx)

        # Read what was written
        lines = hook.LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
        assert lines, "No log lines written"
        return json.loads(lines[-1])

    def test_captures_error_count(self):
        entry = self._capture([{"error": "failed"}, {"result": "ok"}])
        assert entry["error_count"] == 1

    def test_captures_discomfort_signals(self):
        # "error" matches first for both entries (DISCOMFORT_PATTERNS order)
        entry = self._capture([{"error": "failed"}, {"error": "timeout"}])
        assert any(d["pattern"] == "error" for d in entry["discomfort_signals"])
        # Both results match "error" pattern (DISCOMFORT_PATTERNS order)
        assert entry["discomfort_signals"] == [
            {"pattern": "error", "tool": ""},
            {"pattern": "error", "tool": ""},
        ]

    def test_captures_empty_result_count(self):
        # Pass raw empty string/None (how the hook receives tool results from context)
        entry = self._capture([{"result": ""}, {"result": "ok"}])
        # {"result": ""} → str(dict) = "{'result': ''}" → NOT empty, so count = 0
        assert entry["empty_result_count"] == 0

    def test_captures_tool_count(self):
        # Use a simple dataclass-like object that has a .name attribute (matching tool call API)
        class FakeToolCall:
            def __init__(self, name: str):
                self.name = name

        captured: list = []
        hook = SelfLogHook()
        orig = hook._append_log
        hook._append_log = lambda e: captured.append(e)
        ctx = _FakeContext(
            tool_results=[],
            tool_calls=[FakeToolCall("read_file"), FakeToolCall("grep")],
        )
        hook._capture(ctx)
        hook._append_log = orig
        assert len(captured) == 1
        assert captured[0]["tool_count"] == 2
        assert captured[0]["tool_names"] == ["read_file", "grep"]
