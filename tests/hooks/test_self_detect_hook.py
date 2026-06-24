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
        raw = '```\n{"findings": [{"type": "behavior", "content": "repeated tool"}]}\n```'
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


class TestSetWorkspace:
    def test_sets_workspace_attribute(self, tmp_path):
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        assert h._workspace == tmp_path


class TestWriteFindingsDoc:
    def test_skips_when_no_workspace(self, tmp_path):
        """set_workspace was never called → no-op, no file created."""
        h = SelfDetectHook()
        h._write_findings_doc([{"id": "abc", "type": "self_bug", "content": "test"}])
        assert not (tmp_path / "framework" / "self_findings.md").exists()

    def test_writes_unresolved_findings(self, tmp_path):
        """Findings not in resolved set → written to doc."""
        h = SelfDetectHook()
        h.RESOLVED_FILE = tmp_path / "resolved_findings.jsonl"
        h.set_workspace(tmp_path)
        h._write_findings_doc([{"id": "abc", "type": "behavior", "content": "repeated tool calls"}])

        doc = tmp_path / "framework" / "self_findings.md"
        assert doc.exists()
        content = doc.read_text(encoding="utf-8")
        assert "Self-Evolution Findings" in content
        assert "abc" in content
        assert "behavior" in content
        assert "repeated tool calls" in content

    def test_filters_resolved_ids(self, tmp_path):
        """Finding ID present in resolved_findings.jsonl → excluded from doc."""
        resolved_file = tmp_path / "resolved_findings.jsonl"
        resolved_file.write_text("abc\n", encoding="utf-8")

        h = SelfDetectHook()
        h.RESOLVED_FILE = resolved_file
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "abc", "type": "behavior", "content": "resolved one"},
            {"id": "def", "type": "self_bug", "content": "unresolved one"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        assert doc.exists()
        content = doc.read_text(encoding="utf-8")
        print(f"RESOLVED_FILE: {h.RESOLVED_FILE}")
        print(f"exists: {h.RESOLVED_FILE.exists()}")
        print(f"content: {h.RESOLVED_FILE.read_text()}")
        # Check the "abc" finding (resolved) is excluded — verify its header is absent
        assert "### abc" not in content, f"finding abc should be filtered but found in:\n{content}"
        # Check the "def" finding (unresolved) is included
        assert "### def" in content, f"finding def should be present but not found in:\n{content}"

    def test_removes_doc_when_all_resolved(self, tmp_path):
        """All findings resolved → existing doc is deleted."""
        resolved_file = tmp_path / "resolved_findings.jsonl"
        resolved_file.write_text("abc\n", encoding="utf-8")

        h = SelfDetectHook()
        h.RESOLVED_FILE = resolved_file
        h.set_workspace(tmp_path)
        doc = tmp_path / "framework" / "self_findings.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("old content")

        h._write_findings_doc([{"id": "abc", "type": "self_bug", "content": "resolved"}])

        assert not doc.exists()

    def test_removes_doc_when_no_findings(self, tmp_path):
        """Empty findings list → existing doc is deleted."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        doc = tmp_path / "framework" / "self_findings.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("old content")

        h._write_findings_doc([])
        assert not doc.exists()

    def test_format_contains_ids_and_types(self, tmp_path):
        """Output contains expected markdown structure."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "abc123", "type": "self_bug", "content": "bug description", "relevance": "causes wrong count"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        assert "### abc123" in content
        assert "**Content**: bug description" in content
        assert "**Relevance**: causes wrong count" in content
        assert "**Resolve**" in content
        assert "echo abc123" in content


class TestRunTurnReflectionExt:
    """Extension: verify _write_findings_doc is called during reflection."""

    @pytest.mark.asyncio
    async def test_calls_write_findings_doc(self, hook):
        entries = [{"iteration": 1, "time": "2025-01-01T00:00:00", "tool_calls": [], "tool_count": 0,
                     "usage": {"total_tokens": 50}, "error": None}]

        with (
            patch.object(hook, "_call_for_findings", AsyncMock(return_value=([{"type": "self_bug", "content": "bug"}], "ok"))),
            patch.object(hook, "_save_findings"),
            patch.object(hook, "_write_findings_doc") as mock_write,
            patch.object(hook, "_append_to_log"),
            patch("nanobot.hooks.self_detect_hook._read_hook_sources", return_value="## source"),
        ):
            await hook._run_turn_reflection(entries)

        mock_write.assert_called_once_with([{"type": "self_bug", "content": "bug"}])

    @pytest.mark.asyncio
    async def test_calls_write_findings_doc_with_no_findings(self, hook):
        """When LLM returns no findings, write is still called with empty list."""
        entries = [{"iteration": 1, "time": "2025-01-01T00:00:00", "tool_calls": [], "tool_count": 0,
                     "usage": {"total_tokens": 50}, "error": None}]

        with (
            patch.object(hook, "_call_for_findings", AsyncMock(return_value=([], "empty_findings"))),
            patch.object(hook, "_save_findings"),
            patch.object(hook, "_write_findings_doc") as mock_write,
            patch.object(hook, "_append_to_log"),
            patch("nanobot.hooks.self_detect_hook._read_hook_sources", return_value="## source"),
        ):
            await hook._run_turn_reflection(entries)

        mock_write.assert_called_once_with([])


class TestWriteFindingsDocEdgeCases:
    """Additional edge cases for _write_findings_doc."""

    def test_skips_findings_without_id(self, tmp_path):
        """Finding with no id field → excluded from output (can't be resolved)."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"type": "behavior", "content": "no id finding"},
            {"id": "valid", "type": "self_bug", "content": "has id"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        assert "no id finding" not in content
        assert "has id" in content

    def test_resolved_file_with_blank_lines(self, tmp_path):
        """Blank lines in resolved file are skipped."""
        h = SelfDetectHook()
        h.RESOLVED_FILE = tmp_path / "resolved_findings.jsonl"
        h.RESOLVED_FILE.write_text("abc\n\n  \ndef\n", encoding="utf-8")
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "abc", "type": "behavior", "content": "resolved"},
            {"id": "def", "type": "behavior", "content": "also resolved"},
            {"id": "ghi", "type": "self_bug", "content": "unresolved"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        assert "### abc" not in content
        assert "### def" not in content
        assert "### ghi" in content

    def test_missing_resolved_file(self, tmp_path):
        """No resolved_findings.jsonl → all findings included."""
        h = SelfDetectHook()
        h.RESOLVED_FILE = tmp_path / "nonexistent.jsonl"
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "abc", "type": "behavior", "content": "included"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        assert "### abc" in content

    def test_multiple_findings_sorted_by_input_order(self, tmp_path):
        """Multiple findings appear in the order they were provided."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "aaa", "type": "behavior", "content": "first"},
            {"id": "bbb", "type": "self_bug", "content": "second"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        aaa_pos = content.index("### aaa")
        bbb_pos = content.index("### bbb")
        assert aaa_pos < bbb_pos, "findings should preserve input order"

    def test_creates_framework_dir_if_not_exists(self, tmp_path):
        """Framework directory is auto-created by mkdir(parents=True)."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        assert not (tmp_path / "framework").exists()

        h._write_findings_doc([{"id": "abc", "type": "behavior", "content": "test"}])

        assert (tmp_path / "framework").exists()
        assert (tmp_path / "framework" / "self_findings.md").exists()

    def test_edge_case_no_type_and_no_relevance(self, tmp_path):
        """Finding with only id and content — minimal required fields."""
        h = SelfDetectHook()
        h.set_workspace(tmp_path)
        h._write_findings_doc([
            {"id": "minimal", "content": "just a note"},
        ])

        doc = tmp_path / "framework" / "self_findings.md"
        content = doc.read_text(encoding="utf-8")
        assert "### minimal" in content
        assert "just a note" in content
        assert "**Relevance**" not in content
