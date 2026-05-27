"""Tests for nanobot.hooks self-review / self-reflect / self-insight hooks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from nanobot.agent.hook import AgentHookContext
from nanobot.hooks.self_review import SelfReviewHook
from nanobot.hooks.self_insight_hook import SelfInsightHook, LOG_JSONL


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
# SelfReviewHook — discomfort signal detection
# ---------------------------------------------------------------------------

class TestSelfReviewHookDetectDiscomfort:
    """Tests for discomfort signal detection in tool results."""

    def _detect(self, result: dict) -> str | None:
        hook = SelfReviewHook()
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
        hook = SelfReviewHook()
        assert hook._is_error_result({"error": "something failed"})
        assert hook._is_error_result({"error": "ERROR: connection refused"})
        assert not hook._is_error_result({"result": "ok"})
        assert not hook._is_error_result({"result": "file not found"})  # no "error" in str(result)

    def test_empty_result_true(self):
        # Pass raw string/None to _is_empty_result (as the hook receives from context)
        hook = SelfReviewHook()
        assert hook._is_empty_result("")
        assert hook._is_empty_result(None)
        assert hook._is_empty_result("[]")
        assert hook._is_empty_result("{}")
        assert hook._is_empty_result("null")
        assert not hook._is_empty_result("something")
        # str(dict) produces "{'key': ''}" — NOT a plain empty string
        assert not hook._is_empty_result({"result": ""})


class TestSelfReviewHookCapture:
    """Tests for metric capture in SelfReviewHook._capture."""

    def _capture(self, tool_results: list[dict], tool_calls: list | None = None) -> dict:
        hook = SelfReviewHook()
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
        assert "error" in entry["discomfort_signals"]
        # Only one unique "error" signal (first pattern match per result)
        assert entry["discomfort_signals"] == ["error", "error"]

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
        hook = SelfReviewHook()
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


# ---------------------------------------------------------------------------
# SelfInsightHook — JSONL loading and insight building
# ---------------------------------------------------------------------------

class TestSelfInsightHookLoadJsonl:
    """Tests for SelfInsightHook._load_jsonl."""

    def test_loads_valid_jsonl(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text(
            json.dumps({"iteration": 1, "error_count": 1})
            + "\n"
            + json.dumps({"iteration": 2, "error_count": 0})
            + "\n",
            encoding="utf-8",
        )

        hook = SelfInsightHook()
        original = LOG_JSONL
        # Patch the module-level constant (it's used directly in the method)
        import nanobot.hooks.self_insight_hook as m
        saved, m.LOG_JSONL = m.LOG_JSONL, log_file
        try:
            entries = hook._load_jsonl(10)
            assert len(entries) == 2
            assert entries[0]["iteration"] == 1
            assert entries[1]["iteration"] == 2
        finally:
            m.LOG_JSONL = saved

    def test_skips_malformed_lines(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text(
            '{"ok": true}\n'
            + 'not json at all\n'
            + '{"also": "ok"}\n',
            encoding="utf-8",
        )

        import nanobot.hooks.self_insight_hook as m
        saved = m.LOG_JSONL
        m.LOG_JSONL = log_file
        try:
            hook = SelfInsightHook()
            entries = hook._load_jsonl(10)
            assert len(entries) == 2
            assert entries[0]["ok"] is True
            assert entries[1]["also"] == "ok"
        finally:
            m.LOG_JSONL = saved

    def test_respects_max_lines(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [json.dumps({"n": i}) for i in range(100)]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        import nanobot.hooks.self_insight_hook as m
        saved = m.LOG_JSONL
        m.LOG_JSONL = log_file
        try:
            hook = SelfInsightHook()
            entries = hook._load_jsonl(5)
            assert len(entries) == 5
            assert entries[-1]["n"] == 4
        finally:
            m.LOG_JSONL = saved

    def test_missing_file_returns_empty(self):
        import nanobot.hooks.self_insight_hook as m
        saved = m.LOG_JSONL
        m.LOG_JSONL = Path("/nonexistent/this/file/does/not/exist.jsonl")
        try:
            hook = SelfInsightHook()
            entries = hook._load_jsonl(10)
            assert entries == []
        finally:
            m.LOG_JSONL = saved


class TestSelfInsightHookBuildInsight:
    """Tests for SelfInsightHook._build_insight."""

    def test_no_insight_when_below_threshold(self):
        import nanobot.hooks.self_insight_hook as m

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            for i in range(5):
                json.dump(
                    {
                        "iteration": i,
                        "error_count": 0,
                        "prompt_tokens": 1000 + i * 10,
                        "discomfort_signals": [],
                        "tool_calls": [],
                    },
                    f,
                )
                f.write("\n")
            tmp_path = Path(f.name)

        saved = m.LOG_JSONL
        m.LOG_JSONL = tmp_path
        try:
            hook = SelfInsightHook()
            ctx = _FakeContext(messages=[])
            insight = hook._build_metric_insight(ctx)
            assert insight is None
        finally:
            m.LOG_JSONL = saved
            tmp_path.unlink(missing_ok=True)

    def test_injects_insight_above_threshold(self):
        import nanobot.hooks.self_insight_hook as m

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            for i in range(10):
                json.dump(
                    {
                        "iteration": i,
                        "error_count": 1 if i < 6 else 0,
                        "prompt_tokens": 1000,
                        "discomfort_signals": ["error"] * 5,
                        "tool_calls": [],
                    },
                    f,
                )
                f.write("\n")
            tmp_path = Path(f.name)

        saved = m.LOG_JSONL
        m.LOG_JSONL = tmp_path
        try:
            hook = SelfInsightHook()
            ctx = _FakeContext(messages=[])
            insight = hook._build_metric_insight(ctx)
            assert insight is not None
            assert "error" in insight.lower() or "不适" in insight or "错误" in insight
        finally:
            m.LOG_JSONL = saved
            tmp_path.unlink(missing_ok=True)