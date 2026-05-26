"""Tests for nanobot.hooks self-review / self-reflect / self-insight hooks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from nanobot.agent.hook import AgentHookContext


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

from nanobot.hooks.self_review import SelfReviewHook


class TestSelfReviewHookDetectDiscomfort:
    """Tests for discomfort signal detection in tool results."""

    def _detect(self, result: dict) -> str | None:
        hook = SelfReviewHook()
        return hook._detect_discomfort(result)

    def test_error_signal(self):
        assert self._detect({"error": "Connection timeout"}) == "error"

    def test_failed_signal(self):
        assert self._detect({"error": "Request failed"}) == "failed"

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
        assert not hook._is_error_result({"result": "file not found"})  # not a top-level error

    def test_empty_result_true(self):
        hook = SelfReviewHook()
        assert hook._is_empty_result({"result": ""})
        assert hook._is_empty_result({"result": None})
        assert hook._is_empty_result({"result": []})
        assert not hook._is_empty_result({"result": "something"})
        assert not hook._is_empty_result({"result": [1, 2]})


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
        entry = self._capture([{"error": "failed"}, {"error": "timeout"}])
        assert "error" in entry["discomfort_signals"]
        assert "timeout" in entry["discomfort_signals"]

    def test_captures_empty_result_count(self):
        entry = self._capture([{"result": ""}, {"result": "ok"}])
        assert entry["empty_result_count"] == 1

    def test_captures_tool_count(self):
        tc = [MagicMock(name="read_file"), MagicMock(name="grep")]
        entry = self._capture([], tool_calls=tc)
        assert entry["tool_count"] == 2


from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# SelfInsightHook — JSONL loading and insight building
# ---------------------------------------------------------------------------

from nanobot.hooks.self_insight_hook import SelfInsightHook


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

        # Patch LOG_JSONL temporarily
        hook = SelfInsightHook()
        original = hook.LOG_JSONL
        hook.LOG_JSONL = log_file

        entries = hook._load_jsonl(10)
        assert len(entries) == 2
        assert entries[0]["iteration"] == 1
        assert entries[1]["iteration"] == 2

        hook.LOG_JSONL = original

    def test_skips_malformed_lines(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text(
            '{"ok": true}\n'
            + 'not json at all\n'
            + '{"also": "ok"}\n',
            encoding="utf-8",
        )

        hook = SelfInsightHook()
        original = hook.LOG_JSONL
        hook.LOG_JSONL = log_file

        entries = hook._load_jsonl(10)
        assert len(entries) == 2
        assert entries[0]["ok"] is True
        assert entries[1]["also"] == "ok"

        hook.LOG_JSONL = original

    def test_respects_max_lines(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [json.dumps({"n": i}) for i in range(100)]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        hook = SelfInsightHook()
        original = hook.LOG_JSONL
        hook.LOG_JSONL = log_file

        entries = hook._load_jsonl(5)
        assert len(entries) == 5
        assert entries[-1]["n"] == 4

        hook.LOG_JSONL = original

    def test_missing_file_returns_empty(self):
        hook = SelfInsightHook()
        hook.LOG_JSONL = Path("/nonexistent/this/file/does/not/exist.jsonl")
        entries = hook._load_jsonl(10)
        assert entries == []


class TestSelfInsightHookBuildInsight:
    """Tests for SelfInsightHook._build_insight."""

    def test_no_insight_when_below_threshold(self):
        hook = SelfInsightHook()
        original = hook.LOG_JSONL
        # Create a temp file with no errors
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
            hook.LOG_JSONL = Path(f.name)

        ctx = _FakeContext(messages=[])
        insight = hook._build_insight(ctx)
        assert insight is None

        hook.LOG_JSONL = original
        Path(f.name).unlink(missing_ok=True)

    def test_injects_insight_above_threshold(self):
        hook = SelfInsightHook()
        original = hook.LOG_JSONL
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            for i in range(10):
                json.dump(
                    {
                        "iteration": i,
                        "error_count": 1 if i < 6 else 0,
                        "prompt_tokens": 1000,
                        "discomfort_signals": ["error", "error", "error", "error", "error"],
                        "tool_calls": [],
                    },
                    f,
                )
                f.write("\n")
            hook.LOG_JSONL = Path(f.name)

        ctx = _FakeContext(messages=[])
        insight = hook._build_insight(ctx)
        assert insight is not None
        assert "error" in insight.lower() or "不适" in insight or "错误" in insight

        hook.LOG_JSONL = original
        Path(f.name).unlink(missing_ok=True)