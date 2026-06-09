"""Tests for SelfDetectHook (metrics accumulation, LLM reflection, findings)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.hooks.self_detect_hook import SelfDetectHook


@pytest.fixture
def hook(tmp_path):
    h = SelfDetectHook(interval=2)
    h.FINDINGS_FILE = tmp_path / "findings.json"
    h.LOG_FILE = tmp_path / "self_log.md"
    return h


class FakeContext:
    def __init__(self, **kwargs):
        self.tool_calls = kwargs.get("tool_calls", [])
        self.tool_results = kwargs.get("tool_results", [])
        self.usage = kwargs.get("usage", {})
        self.iteration = kwargs.get("iteration", 1)
        self.error = kwargs.get("error")
        self.final_content = kwargs.get("final_content")
        self.messages = kwargs.get("messages", [])


class TestBuildEntry:
    def test_builds_correct_dict(self):
        hook = SelfDetectHook()
        tc1 = MagicMock()
        tc1.name = "web_search_tool"
        tc1.arguments = {"q": "test"}
        ctx = FakeContext(
            tool_calls=[tc1],
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            iteration=3,
            final_content="done",
        )
        entry = hook._build_entry(ctx)
        assert entry["iteration"] == 3
        assert entry["tool_count"] == 1
        assert entry["tool_calls"][0]["name"] == "web_search_tool"
        assert entry["usage"]["total_tokens"] == 150
        assert entry["final_content_len"] == 4
        assert entry["error"] is None
        assert "time" in entry

    def test_filters_self_insight_messages(self):
        hook = SelfDetectHook()
        ctx = FakeContext(messages=[
            {"role": "user", "content": "hi"},
            {"_source": "self_fix_hook", "content": "insight"},
            {"role": "assistant", "content": "ok"},
        ])
        entry = hook._build_entry(ctx)
        assert entry["message_count"] == 2

    def test_empty_inputs(self):
        hook = SelfDetectHook()
        ctx = FakeContext()
        entry = hook._build_entry(ctx)
        assert entry["tool_count"] == 0
        assert entry["usage"]["total_tokens"] == 0
        assert entry["final_content_len"] == 0
        assert entry["error"] is None
        assert entry["message_count"] == 0


class TestAfterIteration:
    @pytest.mark.asyncio
    async def test_accumulates_entries(self, hook):
        ctx = FakeContext(iteration=1)
        await hook.after_iteration(ctx)
        assert len(hook._entries_accumulated) == 1
        assert hook._entries_accumulated[0]["iteration"] == 1

    @pytest.mark.asyncio
    async def test_multiple_calls(self, hook):
        await hook.after_iteration(FakeContext(iteration=1))
        await hook.after_iteration(FakeContext(iteration=2))
        assert len(hook._entries_accumulated) == 2

    @pytest.mark.asyncio
    async def test_exception_safe(self, hook):
        class BadContext:
            tool_calls = None
            tool_results = None
            usage = None
            iteration = "not-an-int"

        await hook.after_iteration(BadContext())
        assert len(hook._entries_accumulated) == 0


class TestAfterTurn:
    @pytest.mark.asyncio
    async def test_early_return_when_no_entries(self, hook):
        await hook.after_turn()
        assert hook._turn_count == 0

    @pytest.mark.asyncio
    async def test_skips_below_interval(self, hook):
        hook._entries_accumulated = [{"iteration": 1}]
        await hook.after_turn()
        assert hook._turn_count == 1
        assert len(hook._entries_accumulated) == 1

    @pytest.mark.asyncio
    async def test_fires_at_interval(self, hook):
        hook._entries_accumulated = [{"iteration": 1}]
        hook._turn_count = 1

        with patch.object(hook, "_run_turn_reflection") as mock_run:
            await hook.after_turn()

        mock_run.assert_awaited_once_with([{"iteration": 1}])
        assert hook._turn_count == 0
        assert hook._entries_accumulated == []


class TestRunTurnReflection:
    @pytest.mark.asyncio
    async def test_builds_summary_and_saves(self, hook):
        entries = [
            {
                "iteration": 1,
                "time": "2025-01-01T00:00:00",
                "tool_calls": [],
                "tool_count": 0,
                "usage": {"total_tokens": 50},
                "error": None,
            },
            {
                "iteration": 2,
                "time": "2025-01-01T00:01:00",
                "tool_calls": [{"name": "read_file_tool", "arguments": {"file_path": "/x"}}],
                "tool_count": 1,
                "usage": {"total_tokens": 100},
                "error": "timeout",
            },
        ]

        with (
            patch.object(hook, "_call_for_findings", AsyncMock(return_value=([{"type": "self_bug", "content": "bug"}], "ok"))) as mock_call,
            patch.object(hook, "_save_findings") as mock_save,
            patch.object(hook, "_append_to_log") as mock_log,
            patch("nanobot.hooks.self_detect_hook._read_hook_sources", return_value="## source"),
        ):
            await hook._run_turn_reflection(entries)

        mock_call.assert_awaited_once()
        assert "Tool frequency" in mock_call.call_args[0][0]
        assert "read_file_tool: 1" in mock_call.call_args[0][0]
        mock_save.assert_called_once()
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_for_findings_empty_on_llm_failure(self, hook):
        with patch.object(hook, "_call_llm", side_effect=RuntimeError("fail")):
            findings, diagnostic = await hook._call_for_findings("metrics", "code")
        assert findings == []
        assert diagnostic == "llm_call_error"


class TestParseFindings:
    def test_from_code_block(self):
        raw = '```json\n{"findings": [{"type": "behavior", "content": "repeated tool"}]}\n```'
        result, diagnostic = SelfDetectHook._parse_findings(raw)
        assert diagnostic == "ok"
        assert len(result) == 1
        assert result[0]["type"] == "behavior"

    def test_plain_json(self):
        raw = '{"findings": [{"type": "self_bug", "content": "wrong count"}]}'
        result, diagnostic = SelfDetectHook._parse_findings(raw)
        assert diagnostic == "ok"
        assert len(result) == 1

    def test_invalid_json_returns_empty(self):
        result, diagnostic = SelfDetectHook._parse_findings("not json at all")
        assert result == []
        assert diagnostic == "json_decode_error"

    def test_filters_invalid_type(self):
        raw = json.dumps({"findings": [
            {"type": "self_bug", "content": "real bug"},
            {"type": "unicorn", "content": "fake"},
        ]})
        result, diagnostic = SelfDetectHook._parse_findings(raw)
        assert diagnostic == "ok"
        assert len(result) == 1
        assert result[0]["type"] == "self_bug"

    def test_missing_type_or_content(self):
        raw = json.dumps({"findings": [
            {"type": "self_bug", "content": "ok"},
            {"type": "behavior"},
            {"content": "orphan"},
        ]})
        result, diagnostic = SelfDetectHook._parse_findings(raw)
        assert diagnostic == "ok"
        assert len(result) == 1

    def test_empty_findings_array(self):
        raw = json.dumps({"findings": []})
        result, diagnostic = SelfDetectHook._parse_findings(raw)
        assert result == []
        assert diagnostic == "empty_findings"


class TestFindingId:
    def test_stable_sha256_prefix(self):
        h = SelfDetectHook()
        id1 = h._finding_id("same content")
        id2 = h._finding_id("same content")
        assert id1 == id2
        assert len(id1) == 12

    def test_different_content_different_id(self):
        h = SelfDetectHook()
        assert h._finding_id("a") != h._finding_id("b")


class TestSaveFindings:
    def test_writes_json_file(self, hook):
        hook._save_findings(
            [{"type": "behavior", "content": "test"}],
            "iter#1", "2025-01-01T00:00:00",
        )
        assert hook.FINDINGS_FILE.exists()
        payload = json.loads(hook.FINDINGS_FILE.read_text())
        assert payload["source"] == "self_detect"
        assert len(payload["findings"]) == 1
        assert "id" in payload["findings"][0]

    def test_adds_id_when_missing(self, hook):
        hook._save_findings(
            [{"type": "self_bug", "content": "no-id"}],
            "#1-#1", "ts",
        )
        payload = json.loads(hook.FINDINGS_FILE.read_text())
        assert payload["findings"][0]["id"] == SelfDetectHook._finding_id("no-id")


class TestAppendToLog:
    def test_writes_findings(self, hook):
        hook._append_to_log("#1", "ts", [], [{"type": "behavior", "content": "x", "relevance": "y"}], "ok")
        log = hook.LOG_FILE.read_text()
        assert "## Turn #1" in log
        assert "**behavior**" in log
        assert "1 finding(s)" in log
        assert "diagnostic: ok" in log

    def test_writes_nothing_actionable_when_empty(self, hook):
        hook._append_to_log("#1", "ts", [], [], "llm_empty")
        log = hook.LOG_FILE.read_text()
        assert "nothing actionable" not in log
        assert "LLM returned empty" in log
        assert "diagnostic: llm_empty" in log

    def test_caps_log_at_max_lines(self, hook):
        for _ in range(20):
            hook.LOG_FILE.write_text("line\n" * 30, encoding="utf-8")
            hook._append_to_log("#x", "ts", [], [], "ok")
        lines = hook.LOG_FILE.read_text().splitlines()
        assert len(lines) <= 210
