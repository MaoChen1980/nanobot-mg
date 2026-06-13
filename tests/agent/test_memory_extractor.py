"""Tests for MemoryExtractor — module-level helpers, static methods, and integration."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.memory_extractor import (
    MemoryExtractor,
    _format_ts,
    _parse_ts,
    _trim_sentence,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider() -> MagicMock:
    return MagicMock()


@pytest.fixture
def extractor(store: MemoryStore) -> MemoryExtractor:
    return MemoryExtractor(store, "test-model")


# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


class TestParseTs:
    def test_none(self) -> None:
        assert _parse_ts(None) is None

    def test_empty_string(self) -> None:
        assert _parse_ts("") is None

    def test_normal_iso(self) -> None:
        result = _parse_ts("2026-06-06T10:30:00")
        assert result is not None
        assert result == pytest.approx(1780713000.0, rel=1e-3)

    def test_non_standard_dashes(self) -> None:
        """ISO 8601 where colons are replaced with dashes (Windows-safe)."""
        result = _parse_ts("2026-06-06T10-30-00")
        assert result is not None
        assert result == pytest.approx(1780713000.0, rel=1e-3)

    def test_invalid_string(self) -> None:
        assert _parse_ts("not-a-date") is None

    def test_invalid_date(self) -> None:
        assert _parse_ts("2026-99-99T10:30:00") is None


# ---------------------------------------------------------------------------
# _format_ts
# ---------------------------------------------------------------------------


class TestFormatTs:
    def test_epoch(self) -> None:
        assert _format_ts(0.0) == "1970-01-01T00:00:00Z"

    def test_known_timestamp(self) -> None:
        ts = _parse_ts("2026-06-06T10:30:00")
        assert ts is not None
        assert ts == pytest.approx(1780713000.0, rel=1e-3)

    def test_roundtrip(self) -> None:
        ts = _parse_ts("2026-06-06T10:30:00")
        assert ts is not None
        # format_ts outputs in UTC (the input date is local UTC+8, so UTC is 02:30)
        formatted = _format_ts(ts)
        assert formatted == "2026-06-06T02:30:00Z"
        # Re-parsing the UTC string and formatting gives the same result
        ts2 = _parse_ts(formatted)
        assert ts2 is not None
        assert _format_ts(ts2) == formatted


# ---------------------------------------------------------------------------
# _trim_sentence
# ---------------------------------------------------------------------------


class TestTrimSentence:
    def test_shorter_than_max(self) -> None:
        assert _trim_sentence("Hello world") == "Hello world"

    def test_exactly_max(self) -> None:
        text = "A" * 150
        assert _trim_sentence(text, 150) == text

    def test_chinese_period(self) -> None:
        text = "研究显示。更多内容在后面"
        assert _trim_sentence(text, 8) == "研究显示。"

    def test_chinese_exclamation_at_40pct(self) -> None:
        """! at index 2 with max_len=4 → 2 > 40% (1.6) → cut at boundary."""
        text = "危险！请勿靠近"
        assert _trim_sentence(text, 4) == "危险！"

    def test_chinese_exclamation_valid_cut(self) -> None:
        """! past 40% threshold -> cut at that boundary."""
        text = "前面的话危险！"
        assert _trim_sentence(text, 10) == "前面的话危险！"  # idx 5 > 4.0

    def test_chinese_question(self) -> None:
        text = "明白了吗？这是重点"
        assert _trim_sentence(text, 8) == "明白了吗？"

    def test_english_period(self) -> None:
        text = "Hello world. More text follows"
        assert _trim_sentence(text, 16) == "Hello world."

    def test_english_exclamation(self) -> None:
        text = "Stop! More text"
        assert _trim_sentence(text, 8) == "Stop!"

    def test_english_question(self) -> None:
        text = "Really? More text"
        assert _trim_sentence(text, 10) == "Really?"

    def test_no_boundary(self) -> None:
        text = "ThisIsALongStringWithNoBoundariesHere"
        result = _trim_sentence(text, 20)
        assert result == "ThisIsALongStringWit…"

    def test_boundary_before_40pct(self) -> None:
        """First boundary before 40% of max_len is skipped; falls through to …."""
        text = "Hi. " + "x" * 50
        result = _trim_sentence(text, 25)
        assert result == "Hi. xxxxxxxxxxxxxxxxxxxxx…"

    def test_multiple_boundaries_last_in_range(self) -> None:
        text = "A. B! C? Rest"
        assert _trim_sentence(text, 10) == "A. B! C?"


# ---------------------------------------------------------------------------
# _format_finding_paragraph
# ---------------------------------------------------------------------------


class TestFormatFindingParagraph:
    def test_pitfall(self) -> None:
        assert MemoryExtractor._format_finding_paragraph("pitfall", "Don't X") == "- ⚠️ Don't X"

    def test_pattern(self) -> None:
        assert MemoryExtractor._format_finding_paragraph("pattern", "Do Y") == "- 💡 Do Y"

    def test_knowledge(self) -> None:
        assert MemoryExtractor._format_finding_paragraph("knowledge", "Fact Z") == "- Fact Z"

    def test_preference(self) -> None:
        assert MemoryExtractor._format_finding_paragraph("preference", "I like W") == "- I like W"

    def test_skill(self) -> None:
        assert MemoryExtractor._format_finding_paragraph("skill", "Can do V") == "- Can do V"


# ---------------------------------------------------------------------------
# _parse_file_paragraphs
# ---------------------------------------------------------------------------


class TestParseFileParagraphs:
    def test_normal_paragraphs(self) -> None:
        text = "# Title\n\nPara1<!--ts:100.0-->\n\nPara2<!--ts:200.0-->\n\n---\n\nfooter"
        result = MemoryExtractor._parse_file_paragraphs(text)
        assert len(result) == 2
        assert result[0]["ts"] == 100.0
        assert result[1]["ts"] == 200.0

    def test_no_ts_marker_defaults_zero(self) -> None:
        text = "# Title\n\nPlain para"
        result = MemoryExtractor._parse_file_paragraphs(text)
        assert len(result) == 1
        assert result[0]["ts"] == 0.0
        assert "Plain para" in result[0]["content"]

    def test_heading_skipped(self) -> None:
        text = "# Title\n\nBody<!--ts:1.0-->"
        result = MemoryExtractor._parse_file_paragraphs(text)
        assert len(result) == 1
        assert "Title" not in result[0]["content"]

    def test_footer_excluded(self) -> None:
        text = "# T\n\nBody<!--ts:1.0-->\n\n---\n\nFoot"
        result = MemoryExtractor._parse_file_paragraphs(text)
        assert len(result) == 1

    def test_empty_text(self) -> None:
        assert MemoryExtractor._parse_file_paragraphs("") == []

    def test_only_heading(self) -> None:
        assert MemoryExtractor._parse_file_paragraphs("# Title") == []

    def test_markers_preserved(self) -> None:
        text = "# T\n\n- content<!--ts:1.0-->\n<!--pinned-->\n<!--recent-->"
        result = MemoryExtractor._parse_file_paragraphs(text)
        assert len(result) == 1
        assert "<!--pinned-->" in result[0]["content"]
        assert "<!--recent-->" in result[0]["content"]


# ---------------------------------------------------------------------------
# _parse_file_structure
# ---------------------------------------------------------------------------


class TestParseFileStructure:
    def test_heading_and_footer(self) -> None:
        text = "# Title\n\nbody\n\n---\n\nfooter"
        h, f = MemoryExtractor._parse_file_structure(text)
        assert h == "# Title"
        assert f == "---\n\nfooter"

    def test_no_heading(self) -> None:
        text = "body\n\n---\n\nfooter"
        h, f = MemoryExtractor._parse_file_structure(text)
        assert h == ""
        assert f == "---\n\nfooter"

    def test_no_footer(self) -> None:
        text = "# Title\n\nbody"
        h, f = MemoryExtractor._parse_file_structure(text)
        assert h == "# Title"
        assert f == ""

    def test_first_heading_used(self) -> None:
        text = "# First\n\nbody\n\n# Second\n\n---\n\nfooter"
        h, f = MemoryExtractor._parse_file_structure(text)
        assert h == "# First"

    def test_last_separator_is_footer(self) -> None:
        text = "# T\n\n---\n\nmid\n\n---\n\nfooter"
        h, f = MemoryExtractor._parse_file_structure(text)
        assert f == "---\n\nfooter"
        assert "mid" not in f

    def test_empty_text(self) -> None:
        h, f = MemoryExtractor._parse_file_structure("")
        assert h == ""
        assert f == ""


# ---------------------------------------------------------------------------
# _supersedes_in_memory
# ---------------------------------------------------------------------------


class TestSupersedesInMemory:
    def test_found_and_replaced(self) -> None:
        state = {"test.md": [{"content": "old text<!--ts:100.0-->", "ts": 100.0}]}
        result = MemoryExtractor._supersedes_in_memory(
            state, "test.md", "old text", "new text<!--ts:200.0-->", 200.0,
        )
        assert result is True
        assert "new text" in state["test.md"][0]["content"]

    def test_found_but_new_ts_equal_skipped(self) -> None:
        state = {"test.md": [{"content": "old text<!--ts:200.0-->", "ts": 200.0}]}
        result = MemoryExtractor._supersedes_in_memory(
            state, "test.md", "old text", "new text<!--ts:300.0-->", 200.0,
        )
        assert result is True
        assert "old text" in state["test.md"][0]["content"]

    def test_found_but_new_ts_older_skipped(self) -> None:
        state = {"test.md": [{"content": "old text<!--ts:200.0-->", "ts": 200.0}]}
        result = MemoryExtractor._supersedes_in_memory(
            state, "test.md", "old text", "new text<!--ts:300.0-->", 100.0,
        )
        assert result is True
        assert "old text" in state["test.md"][0]["content"]

    def test_not_found(self) -> None:
        state = {"test.md": [{"content": "other content", "ts": 100.0}]}
        result = MemoryExtractor._supersedes_in_memory(
            state, "test.md", "missing", "new<!--ts:200.0-->", 200.0,
        )
        assert result is False

    def test_case_insensitive_match(self) -> None:
        state = {"test.md": [{"content": "Old Text Here<!--ts:100.0-->", "ts": 100.0}]}
        result = MemoryExtractor._supersedes_in_memory(
            state, "test.md", "old text", "new text<!--ts:200.0-->", 200.0,
        )
        assert result is True


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_normal_name(self) -> None:
        assert MemoryExtractor._sanitize_filename("hello_world") == "hello_world"

    def test_special_chars_replaced(self) -> None:
        result = MemoryExtractor._sanitize_filename("hello:world/test*name")
        assert ":" not in result
        assert "/" not in result
        assert "*" not in result

    def test_truncated_at_64(self) -> None:
        name = "a" * 100
        assert len(MemoryExtractor._sanitize_filename(name)) == 64

    def test_strip_leading_trailing_underscore(self) -> None:
        assert MemoryExtractor._sanitize_filename("_hello_") == "hello"

    def test_spaces_replaced(self) -> None:
        assert MemoryExtractor._sanitize_filename("harness design") == "harness_design"


# ---------------------------------------------------------------------------
# _topic_to_filepath
# ---------------------------------------------------------------------------


class TestTopicToFilepath:
    def test_simple_topic(self) -> None:
        path = MemoryExtractor._topic_to_filepath("harness design")
        assert path == "harness_design"

    def test_hierarchical(self) -> None:
        path = MemoryExtractor._topic_to_filepath("AI/harness design")
        assert path == "AI/harness_design"

    def test_path_traversal_blocked(self) -> None:
        path = MemoryExtractor._topic_to_filepath("../etc/passwd")
        assert ".." not in path
        assert path == "etc/passwd"

    def test_max_depth(self) -> None:
        path = MemoryExtractor._topic_to_filepath("a/b/c/d/e/f/g/h/i/j")
        assert path.count("/") == 7  # 8 levels

    def test_sanitize_each_part(self) -> None:
        path = MemoryExtractor._topic_to_filepath("AI:research/harness:design")
        assert ":" not in path

    def test_empty_parts_removed(self) -> None:
        path = MemoryExtractor._topic_to_filepath("AI//harness")
        assert "//" not in path
        assert path == "AI/harness"


# ---------------------------------------------------------------------------
# _parse_json_output
# ---------------------------------------------------------------------------


class TestParseJsonOutput:
    def test_valid_json_with_findings(self) -> None:
        raw = '{"findings": [{"type": "knowledge", "content": "test"}]}'
        result = MemoryExtractor._parse_json_output(raw)
        assert result is not None
        assert len(result["findings"]) == 1

    def test_invalid_json_returns_none(self) -> None:
        raw = "not json"
        assert MemoryExtractor._parse_json_output(raw) is None

    def test_missing_findings_key_returns_none(self) -> None:
        raw = '{"other": 1}'
        assert MemoryExtractor._parse_json_output(raw) is None

    def test_findings_not_a_list_reset(self) -> None:
        raw = '{"findings": "string"}'
        result = MemoryExtractor._parse_json_output(raw)
        assert result is not None
        assert result["findings"] == []

    def test_invalid_finding_missing_type_filtered(self) -> None:
        raw = '{"findings": [{"content": "c"}]}'
        result = MemoryExtractor._parse_json_output(raw)
        assert result is not None
        assert result["findings"] == []

    def test_invalid_finding_missing_content_filtered(self) -> None:
        raw = '{"findings": [{"type": "knowledge"}]}'
        result = MemoryExtractor._parse_json_output(raw)
        assert result is not None
        assert result["findings"] == []

    def test_custom_required_key(self) -> None:
        raw = '{"suggestions": [{"action": "keep"}]}'
        result = MemoryExtractor._parse_json_output(raw, required_key="suggestions")
        assert result is not None
        assert len(result["suggestions"]) == 1

    def test_valid_and_invalid_mixed(self) -> None:
        raw = '{"findings": [{"type": "k", "content": "c"}, {"type": "k"}]}'
        result = MemoryExtractor._parse_json_output(raw)
        assert result is not None
        assert len(result["findings"]) == 1


# ---------------------------------------------------------------------------
# _snapshot_memory_dir
# ---------------------------------------------------------------------------


class TestSnapshotMemoryDir:
    def test_empty_dir(self, extractor: MemoryExtractor) -> None:
        assert MemoryExtractor._snapshot_memory_dir(extractor.store.memory_dir) == {}

    def test_some_files(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "foo.md").write_text("content", encoding="utf-8")
        sub = mem_dir / "bar"
        sub.mkdir()
        (sub / "baz.md").write_text("content", encoding="utf-8")
        result = MemoryExtractor._snapshot_memory_dir(mem_dir)
        assert "foo.md" in result
        assert "bar" + os.sep + "baz.md" in result

    def test_excludes_memory_md(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "MEMORY.md").write_text("mem", encoding="utf-8")
        result = MemoryExtractor._snapshot_memory_dir(mem_dir)
        assert "MEMORY.md" not in result

    def test_excludes_index_md(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        sub = mem_dir / "sub"
        sub.mkdir()
        (sub / "index.md").write_text("idx", encoding="utf-8")
        result = MemoryExtractor._snapshot_memory_dir(mem_dir)
        assert "sub" + os.sep + "index.md" not in result

    def test_excludes_vector_index_dir(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        vi = mem_dir / ".vector_index"
        vi.mkdir()
        (vi / "data.md").write_text("data", encoding="utf-8")
        result = MemoryExtractor._snapshot_memory_dir(mem_dir)
        assert ".vector_index" + os.sep + "data.md" not in result


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — helpers
# ---------------------------------------------------------------------------


def _make_finding(
    ftype: str = "knowledge",
    content: str = "test content",
    topic: str = "test/topic",
    ts: str | None = None,
    recent: bool = False,
    pinned: bool = False,
    supersedes: str = "",
    name: str = "",
) -> dict:
    d: dict = {"type": ftype, "content": content}
    if topic:
        d["topic"] = topic
    if ts:
        d["ts"] = ts
    if recent:
        d["recent"] = True
    if pinned:
        d["pinned"] = True
    if supersedes:
        d["supersedes"] = supersedes
    if name:
        d["name"] = name
    return d


def _read_paragraphs(path: Path) -> list[str]:
    """Return non-heading, non-footer paragraphs from a written memory file.

    Paragraphs are separated by blank lines (``\\n\\n``).  Multi-line content
    within a single paragraph (e.g. ts/pinned/recent markers on their own line)
    is joined.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    raw_paragraphs = re.split(r"\n\n+", text.strip())
    result: list[str] = []
    heading_skipped = False
    for p in raw_paragraphs:
        p = p.strip()
        if not p:
            continue
        if not heading_skipped and p.startswith("# "):
            heading_skipped = True
            continue
        if p == "---":
            break
        result.append(p)
    return result


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — empty / filter tests
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildFilter:
    @pytest.mark.asyncio
    async def test_no_findings_returns_none(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild([])
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_type_returns_none(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="skip", content="x", topic="")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="  ", topic="t")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_vague_advice_quality_gate(self, extractor: MemoryExtractor) -> None:
        """Chinese 注意：…的了 pattern triggers quality gate."""
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="注意：性能问题了", topic="t")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_vague_suggestion_quality_gate(self, extractor: MemoryExtractor) -> None:
        """Chinese 建议：…的 pattern triggers quality gate."""
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="建议：优化方案的", topic="t")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_vague_optimization_quality_gate(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="优化代码了", topic="t")]
        )
        assert result is None


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — write by type
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildByType:
    @pytest.mark.asyncio
    async def test_preference_written_to_user_md(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="preference", content="Likes Python", topic="")]
        )
        assert result is not None
        user_file = extractor.store.user_file
        assert user_file.exists()
        text = user_file.read_text(encoding="utf-8")
        assert "Likes Python" in text
        assert "<!--ts:" in text

    @pytest.mark.asyncio
    async def test_knowledge_written_to_topic_file(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="knowledge", content="some fact", topic="Python/async")]
        )
        assert result is not None
        topic_file = extractor.store.memory_dir / "Python" / "async.md"
        assert topic_file.exists()
        text = topic_file.read_text(encoding="utf-8")
        assert "some fact" in text

    @pytest.mark.asyncio
    async def test_knowledge_without_topic_skipped(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="knowledge", content="fact", topic="")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_pitfall_written_with_prefix(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="pitfall", content="Don't X", topic="Python")]
        )
        topic_file = extractor.store.memory_dir / "Python.md"
        text = topic_file.read_text(encoding="utf-8")
        assert "⚠️" in text
        assert "Don't X" in text

    @pytest.mark.asyncio
    async def test_pattern_with_name_bold(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="pattern", content="Do Y", topic="Python", name="DRY")]
        )
        topic_file = extractor.store.memory_dir / "Python.md"
        text = topic_file.read_text(encoding="utf-8")
        assert "**DRY**" in text

    @pytest.mark.asyncio
    async def test_pattern_without_name(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="pattern", content="Do Y", topic="Python", name="")]
        )
        topic_file = extractor.store.memory_dir / "Python.md"
        text = topic_file.read_text(encoding="utf-8")
        assert "💡" in text
        assert "**" not in text

    @pytest.mark.asyncio
    async def test_instruction_written_to_rules_md(self, extractor: MemoryExtractor) -> None:
        """instruction type finding → written to RULES.md."""
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="instruction", content="必ずテストを実行してからコミットする", topic="")]
        )
        assert result is not None
        rules_file = extractor.store.rules_file
        assert rules_file.exists()
        text = rules_file.read_text(encoding="utf-8")
        assert "必ずテストを実行してからコミットする" in text
        assert "<!--ts:" in text

    @pytest.mark.asyncio
    async def test_instruction_appended_to_existing_rules_md(self, extractor: MemoryExtractor) -> None:
        """Existing RULES.md content preserved when adding new instruction."""
        rules_file = extractor.store.rules_file
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        rules_file.write_text("# Rules\n\n- old rule<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n", encoding="utf-8")
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="instruction", content="新しいルール", topic="", ts="2026-06-01T00:00:00")]
        )
        text = rules_file.read_text(encoding="utf-8")
        assert "old rule" in text
        assert "新しいルール" in text

    @pytest.mark.asyncio
    async def test_instruction_without_topic_is_valid(self, extractor: MemoryExtractor) -> None:
        """instruction type does not require topic (unlike knowledge)."""
        result = await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="instruction", content="no hardcoded secrets", topic="")]
        )
        assert result is not None
        text = extractor.store.rules_file.read_text(encoding="utf-8")
        assert "no hardcoded secrets" in text

    @pytest.mark.asyncio
    async def test_skill_written_to_pending_skills(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(ftype="skill", content="useful skill", topic="", name="my-skill")]
        )
        pending = extractor.store.memory_dir / "pending_skills.md"
        assert pending.exists()
        text = pending.read_text(encoding="utf-8")
        assert "**my-skill**" in text
        assert "useful skill" in text
        assert "<!--ts:" in text


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — markers
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildMarkers:
    @pytest.mark.asyncio
    async def test_pinned_marker_appended(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="important", topic="t", pinned=True)]
        )
        path = extractor.store.memory_dir / "t.md"
        text = path.read_text(encoding="utf-8")
        assert "<!--pinned-->" in text

    @pytest.mark.asyncio
    async def test_recent_marker_appended(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild(
            [_make_finding(content="recent fact", topic="t", recent=True)]
        )
        path = extractor.store.memory_dir / "t.md"
        text = path.read_text(encoding="utf-8")
        assert "<!--recent-->" in text


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — supersedes
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildSupersedes:
    @pytest.mark.asyncio
    async def test_supersedes_in_memory(self, extractor: MemoryExtractor) -> None:
        """Supersedes chains within the same batch — old replaced by new."""
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="old info", topic="t", ts="2026-01-01T00:00:00"),
            _make_finding(
                content="new info", topic="t", ts="2026-06-01T00:00:00",
                supersedes="old info",
            ),
        ])
        path = extractor.store.memory_dir / "t.md"
        paragraphs = _read_paragraphs(path)
        assert len(paragraphs) == 1
        assert "new info" in paragraphs[0]

    @pytest.mark.asyncio
    async def test_supersedes_existing_file_content(self, extractor: MemoryExtractor) -> None:
        """Supersedes removes matching paragraphs from existing file when new ts > old."""
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n- old info<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="new info", topic="t", ts="2026-06-01T00:00:00",
                          supersedes="old info"),
        ])
        paragraphs = _read_paragraphs(path)
        combined = " ".join(paragraphs)
        assert "old info" not in combined
        assert "new info" in combined

    @pytest.mark.asyncio
    async def test_supersedes_older_keeps_both(self, extractor: MemoryExtractor) -> None:
        """New entry with supersedes but older ts — both entries survive."""
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n- old info<!--ts:1767283200.0-->\n\n---\n\n*更新: 2026-01-02*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="new info", topic="t", ts="2026-01-01T00:00:00",
                          supersedes="old info"),
        ])
        paragraphs = _read_paragraphs(path)
        combined = " ".join(paragraphs)
        assert "old info" in combined
        assert "new info" in combined


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — merge, dedup, orphaned headings
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildMerge:
    @pytest.mark.asyncio
    async def test_existing_file_merged_with_new(self, extractor: MemoryExtractor) -> None:
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n- existing<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="new entry", topic="t", ts="2026-06-01T00:00:00"),
        ])
        paragraphs = _read_paragraphs(path)
        assert len(paragraphs) == 2

    @pytest.mark.asyncio
    async def test_dedup_identical_content(self, extractor: MemoryExtractor) -> None:
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n- same content<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="same content", topic="t", ts="2026-06-01T00:00:00"),
        ])
        paragraphs = _read_paragraphs(path)
        assert len(paragraphs) == 1

    @pytest.mark.asyncio
    async def test_dedup_strips_markers(self, extractor: MemoryExtractor) -> None:
        """Dedup normalizes by stripping ts/pinned/recent markers."""
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="same", topic="t", ts="2026-01-01T00:00:00", recent=True),
            _make_finding(content="same", topic="t", ts="2026-06-01T00:00:00", pinned=True),
        ])
        path = extractor.store.memory_dir / "t.md"
        paragraphs = _read_paragraphs(path)
        assert len(paragraphs) == 1

    @pytest.mark.asyncio
    async def test_orphaned_heading_removed(self, extractor: MemoryExtractor) -> None:
        """## heading becomes orphaned when its only content is superseded."""
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n## Section\n\n- old body<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([
            _make_finding(
                content="new body", topic="t", ts="2026-06-01T00:00:00",
                supersedes="old body",
            ),
        ])
        texts = _read_paragraphs(path)
        combined = " ".join(texts)
        # "## Section" is kept because "new body" follows it (not orphaned anymore)
        assert "## Section" in combined
        assert "old body" not in combined
        assert "new body" in combined

    @pytest.mark.asyncio
    async def test_orphaned_heading_with_content_kept(self, extractor: MemoryExtractor) -> None:
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# t\n\n## Section\n\n- body<!--ts:100.0-->\n\n---\n\n*更新: 2026-01-01*\n",
            encoding="utf-8",
        )
        await extractor._write_cleanup_and_rebuild([])
        texts = _read_paragraphs(path)
        combined = " ".join(texts)
        assert "## Section" in combined
        assert "body" in combined


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — recent_entries collection
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildRecent:
    @pytest.mark.asyncio
    async def test_recent_entries_collected(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild([
            _make_finding(content="recent fact", topic="t", ts="2026-06-01T00:00:00",
                          recent=True),
        ])
        assert result is not None
        assert len(result) == 1
        assert result[0]["topic"] == "t.md"
        assert "recent fact" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_no_recent_marker_excluded(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild([
            _make_finding(content="not recent", topic="t", ts="2026-06-01T00:00:00",
                          recent=False),
        ])
        assert result is not None
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_recent_sorted_newest_first(self, extractor: MemoryExtractor) -> None:
        result = await extractor._write_cleanup_and_rebuild([
            _make_finding(content="old", topic="t", ts="2026-01-01T00:00:00",
                          recent=True),
            _make_finding(content="new", topic="t", ts="2026-06-01T00:00:00",
                          recent=True),
        ])
        assert result is not None
        assert "- new" in result[0]["content"]
        assert "- old" in result[1]["content"]

    @pytest.mark.asyncio
    async def test_recent_max_12(self, extractor: MemoryExtractor) -> None:
        findings = [
            _make_finding(content=f"entry-{i}", topic="t", ts=f"2026-06-{i+1:02d}T00:00:00",
                          recent=True)
            for i in range(15)
        ]
        result = await extractor._write_cleanup_and_rebuild(findings)
        assert result is not None
        assert len(result) == 12

    @pytest.mark.asyncio
    async def test_recent_content_truncated_at_200(self, extractor: MemoryExtractor) -> None:
        long_content = "x" * 300
        result = await extractor._write_cleanup_and_rebuild([
            _make_finding(content=long_content, topic="t", ts="2026-06-01T00:00:00",
                          recent=True),
        ])
        assert result is not None
        assert len(result[0]["content"]) <= 200


# ---------------------------------------------------------------------------
# _write_cleanup_and_rebuild — file structure / write mechanics
# ---------------------------------------------------------------------------


class TestWriteCleanupAndRebuildStructure:
    @pytest.mark.asyncio
    async def test_heading_generated_if_missing(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="fact", topic="python/async"),
        ])
        path = extractor.store.memory_dir / "python" / "async.md"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# async\n")

    @pytest.mark.asyncio
    async def test_existing_heading_preserved(self, extractor: MemoryExtractor) -> None:
        path = extractor.store.memory_dir / "t.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Custom Title\n\n---\n\n*更新: old*\n", encoding="utf-8")
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="fact", topic="t", ts="2026-06-01T00:00:00"),
        ])
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Custom Title\n")

    @pytest.mark.asyncio
    async def test_footer_with_date(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="fact", topic="t"),
        ])
        path = extractor.store.memory_dir / "t.md"
        text = path.read_text(encoding="utf-8")
        assert "---" in text
        assert "*更新:" in text

    @pytest.mark.asyncio
    async def test_tmp_file_cleaned_up(self, extractor: MemoryExtractor) -> None:
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="fact", topic="t"),
        ])
        path = extractor.store.memory_dir / "t.md"
        tmp_path_p = path.with_suffix(".md.tmp")
        assert not tmp_path_p.exists()

    @pytest.mark.asyncio
    async def test_atomic_write_does_not_lose_data(self, extractor: MemoryExtractor) -> None:
        """Write twice, verify both entries survive and tmp is cleaned."""
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="first", topic="t", ts="2026-01-01T00:00:00"),
        ])
        await extractor._write_cleanup_and_rebuild([
            _make_finding(content="second", topic="t", ts="2026-06-01T00:00:00"),
        ])
        path = extractor.store.memory_dir / "t.md"
        tmp_path_p = path.with_suffix(".md.tmp")
        assert not tmp_path_p.exists()
        paragraphs = _read_paragraphs(path)
        assert len(paragraphs) == 2


# ---------------------------------------------------------------------------
# _generate_memory_index
# ---------------------------------------------------------------------------


class TestGenerateMemoryIndex:
    def test_empty_memory_dir_noop(self, extractor: MemoryExtractor) -> None:
        """No files → MEMORY.md not created."""
        extractor._generate_memory_index([])
        mem_file = extractor.store.memory_file
        assert not mem_file.exists()

    def test_single_file_indexed(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "test.md").write_text("# Test Topic\ncontent\n", encoding="utf-8")
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "Test Topic" in text

    def test_pinned_section_included(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "test.md").write_text(
            "# Test\n- important item\n<!--pinned-->\n", encoding="utf-8",
        )
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "## Pinned" in text
        assert "important item" in text

    def test_pinned_max_6(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        for i in range(8):
            (mem_dir / f"file{i}.md").write_text(
                f"# File {i}\n- item{i}\n<!--pinned-->\n", encoding="utf-8",
            )
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        pinned_lines = [l for l in text.split("\n") if l.startswith("- [")]
        assert len(pinned_lines) <= 6

    def test_recent_changes_section(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "test.md").write_text("# Test\ncontent\n", encoding="utf-8")
        now = time.time()
        extractor._generate_memory_index([
            {"content": "new fact", "ts": now - 100, "topic": "test.md"},
        ])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "## Recent changes" in text
        assert "**new fact**" in text

    def test_recent_older_than_two_days_not_bold(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "test.md").write_text("# Test\ncontent\n", encoding="utf-8")
        old_ts = time.time() - 200_000
        extractor._generate_memory_index([
            {"content": "old fact", "ts": old_ts, "topic": "test.md"},
        ])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "old fact" in text
        assert "**old fact**" not in text

    def test_category_clickable(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        dev_dir = mem_dir / "Dev"
        dev_dir.mkdir()
        (dev_dir / "test.md").write_text("# Dev Topic\ncontent\n", encoding="utf-8")
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "Dev/index.md" in text

    def test_20_category_limit(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        for i in range(25):
            d = mem_dir / f"cat{i}"
            d.mkdir()
            (d / "file.md").write_text(f"# Cat {i}\ncontent\n", encoding="utf-8")
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        cat_lines = [l for l in text.split("\n") if l.startswith("- **")]
        assert len(cat_lines) <= 20

    def test_file_without_heading_uses_stem(self, extractor: MemoryExtractor) -> None:
        mem_dir = extractor.store.memory_dir
        (mem_dir / "topic.md").write_text("just content\n", encoding="utf-8")
        extractor._generate_memory_index([])
        text = extractor.store.memory_file.read_text(encoding="utf-8")
        assert "topic" in text
