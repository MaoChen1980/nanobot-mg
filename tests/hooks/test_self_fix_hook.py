"""Tests for SelfFixHook (finding injection into agent context)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from nanobot.hooks.self_fix_hook import SelfFixHook
from nanobot.hooks._utils import read_resolved_ids as _read_resolved_ids


class FakeContext:
    def __init__(self, **kwargs):
        self.messages = kwargs.get("messages", [{"role": "system", "content": "sys"},
                                                  {"role": "user", "content": "hi"}])
        self.iteration = kwargs.get("iteration", 1)


@pytest.fixture
def hook(tmp_path):
    h = SelfFixHook()
    h._last_injected = ""
    h._reported_ids = set()
    h.FINDINGS_FILE = tmp_path / "findings.json"  # isolate from real findings
    return h


class TestReadResolvedIds:
    def test_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        with patch("nanobot.hooks._utils.RESOLVED_FILE", path):
            assert _read_resolved_ids() == set()

    def test_reads_ids_from_jsonl(self, tmp_path):
        path = tmp_path / "resolved.jsonl"
        path.write_text("abc\n123\ndef\n", encoding="utf-8")
        with patch("nanobot.hooks._utils.RESOLVED_FILE", path):
            assert _read_resolved_ids() == {"abc", "123", "def"}

    def test_oserror_returns_empty(self, tmp_path):
        mock_file = MagicMock()
        mock_file.exists.return_value = True
        mock_file.read_text.side_effect = OSError()
        with patch("nanobot.hooks._utils.RESOLVED_FILE", mock_file):
            assert _read_resolved_ids() == set()

    def test_max_ids_caps(self, tmp_path):
        path = tmp_path / "resolved.jsonl"
        ids = "\n".join([f"id{i:04d}" for i in range(50)])
        path.write_text(ids + "\n", encoding="utf-8")
        with patch("nanobot.hooks._utils.RESOLVED_FILE", path):
            result = _read_resolved_ids(max_ids=10)
        assert len(result) == 10


class TestBuildFindingInsight:
    def test_missing_file_returns_none(self, hook, tmp_path):
        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", tmp_path / "nonexistent.json"):
            assert hook._build_finding_insight() is None

    def test_json_error_returns_none(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text("{invalid json", encoding="utf-8")
        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file):
            assert hook._build_finding_insight() is None

    def test_filters_resolved_ids(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps({
            "findings": [
                {"id": "aaa", "type": "self_bug", "content": "unresolved bug"},
                {"id": "bbb", "type": "behavior", "content": "resolved"},
            ],
        }), encoding="utf-8")
        resolved_file = tmp_path / "resolved.jsonl"
        resolved_file.write_text("bbb\n", encoding="utf-8")

        with (
            patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file),
            patch("nanobot.hooks._utils.RESOLVED_FILE", resolved_file),
        ):
            result = hook._build_finding_insight()

        assert result is not None
        assert "[aaa]" in result
        assert "[bbb]" not in result

    def test_filters_reported_ids(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps({
            "findings": [
                {"id": "ccc", "type": "self_bug", "content": "already reported"},
            ],
        }), encoding="utf-8")
        hook._reported_ids.add("ccc")

        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file):
            assert hook._build_finding_insight() is None

    def test_max_three_findings(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps({
            "findings": [
                {"id": f"f{i:03d}", "type": "behavior", "content": f"finding {i}"}
                for i in range(10)
            ],
        }), encoding="utf-8")

        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file):
            result = hook._build_finding_insight()

        assert result is not None
        count = result.count("(behavior)")
        assert count == 3

    def test_empty_findings_returns_none(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps({"findings": []}), encoding="utf-8")
        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file):
            assert hook._build_finding_insight() is None

    def test_missing_ids_skipped(self, hook, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps({
            "findings": [
                {"type": "behavior", "content": "no id"},
            ],
        }), encoding="utf-8")
        with patch("nanobot.hooks.self_fix_hook.FINDINGS_FILE", findings_file):
            assert hook._build_finding_insight() is None


class TestInjectInsight:
    def test_inserts_after_system_message(self):
        hook = SelfFixHook()
        ctx = FakeContext(messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ])
        hook._inject_insight(ctx, "test insight")
        assert len(ctx.messages) == 4  # sys + user reminder + assistant ack + original hi
        assert ctx.messages[1]["_source"] == "self_fix_hook"
        assert ctx.messages[1]["role"] == "user"
        assert "[Self-Fix from your history]" in ctx.messages[1]["content"]
        assert ctx.messages[2]["_source"] == "self_fix_hook"
        assert ctx.messages[2]["role"] == "assistant"
        assert "[Self-Fix acknowledged]" in ctx.messages[2]["content"]

    def test_inserts_at_zero_when_no_system(self):
        hook = SelfFixHook()
        ctx = FakeContext(messages=[
            {"role": "user", "content": "hi"},
        ])
        hook._inject_insight(ctx, "test")
        assert ctx.messages[0]["_source"] == "self_fix_hook"
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[1]["_source"] == "self_fix_hook"
        assert ctx.messages[1]["role"] == "assistant"

    def test_removes_stale_entries_first(self):
        hook = SelfFixHook()
        ctx = FakeContext(messages=[
            {"role": "system", "content": "sys"},
            {"_source": "self_fix_hook", "content": "old"},
            {"role": "user", "content": "hi"},
        ])
        hook._inject_insight(ctx, "new insight")
        sources = [m.get("_source") for m in ctx.messages]
        assert sources.count("self_fix_hook") == 2  # user + assistant pair


class TestBeforeIteration:
    @pytest.mark.asyncio
    async def test_no_insight_noop(self, hook):
        ctx = FakeContext()
        with patch.object(hook, "_build_finding_insight", return_value=None):
            await hook.before_iteration(ctx)
            assert len(ctx.messages) == 2

    @pytest.mark.asyncio
    async def test_dedup_by_last_injected(self, hook):
        hook._last_injected = "same insight"
        ctx = FakeContext()
        with patch.object(hook, "_build_finding_insight", return_value="same insight"):
            await hook.before_iteration(ctx)
            assert len(ctx.messages) == 2

    @pytest.mark.asyncio
    async def test_injects_and_updates_last_injected(self, hook):
        ctx = FakeContext()
        hook._disabled = False  # enable hook (default is disabled)
        with patch.object(hook, "_build_finding_insight", return_value="new insight"):
            await hook.before_iteration(ctx)
            assert hook._last_injected == "new insight"
            assert len(ctx.messages) == 4  # sys + user + assistant + original hi
            assert ctx.messages[1]["_source"] == "self_fix_hook"
            assert ctx.messages[2]["_source"] == "self_fix_hook"

    @pytest.mark.asyncio
    async def test_exception_safe(self, hook):
        ctx = FakeContext()
        with patch.object(hook, "_build_finding_insight", side_effect=RuntimeError):
            await hook.before_iteration(ctx)
            assert len(ctx.messages) == 2
