"""Tests for RecallTool — memory search tool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.recall import RecallTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> MemoryStore:
    """Build a MemoryStore with some test data."""
    store = MemoryStore(tmp_path)
    store.write_memory("Line about Python.\nLine about Java.")
    store.append_history("User asked about Python yesterday")
    store.append_history("Assistant explained Python basics")
    return store


def _make_tool(store: MemoryStore) -> RecallTool:
    return RecallTool(store=store)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecallToolBasic:
    """Basic RecallTool properties."""

    def test_tool_name(self, tmp_path: Path):
        tool = _make_tool(_make_store(tmp_path))
        assert tool.name == "recall"

    def test_tool_description(self, tmp_path: Path):
        tool = _make_tool(_make_store(tmp_path))
        assert "memories" in tool.description.lower()
        assert "SQLite" in tool.description or "MEMORY" in tool.description

    def test_tool_is_read_only(self, tmp_path: Path):
        tool = _make_tool(_make_store(tmp_path))
        assert tool.read_only is True

    def test_parameters_has_start_end_keyword(self, tmp_path: Path):
        tool = _make_tool(_make_store(tmp_path))
        params = tool.parameters
        assert "start" in params["properties"]
        assert "end" in params["properties"]
        assert "keyword" in params["properties"]


class TestRecallToolExecute:
    """RecallTool.execute() behavior."""

    @pytest.mark.asyncio
    async def test_execute_no_params_returns_all(self, tmp_path: Path):
        """With no date filter, returns all memories."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        result = await tool.execute()
        assert "Python" in result
        assert "Java" in result

    @pytest.mark.asyncio
    async def test_execute_with_keyword_filters(self, tmp_path: Path):
        """Keyword filter reduces results."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        result = await tool.execute(keyword="Python")
        assert "Python" in result
        assert "No memories found" not in result

    @pytest.mark.asyncio
    async def test_execute_with_non_matching_keyword(self, tmp_path: Path):
        """Non-matching keyword returns empty."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        result = await tool.execute(keyword="Ruby")
        assert "No memories found" in result

    @pytest.mark.asyncio
    async def test_execute_with_date_range(self, tmp_path: Path):
        """Date range filters history entries."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        today = datetime.now().strftime("%Y-%m-%d")
        result = await tool.execute(start=today, end=today)
        assert "Python" in result or "No memories found" in result

    @pytest.mark.asyncio
    async def test_execute_empty_memory_returns_no_memories(self, tmp_path: Path):
        """Empty memory store returns appropriate message."""
        store = MemoryStore(tmp_path)
        tool = _make_tool(store)
        result = await tool.execute()
        assert "No memories found" in result

    @pytest.mark.asyncio
    async def test_execute_result_has_section_header(self, tmp_path: Path):
        """Results are formatted with section header."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        result = await tool.execute()
        assert "## Relevant Memories" in result


class TestRecallToolDateParsing:
    """Date parsing edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_date_format_falls_back(self, tmp_path: Path):
        """Invalid date format is handled gracefully."""
        store = _make_store(tmp_path)
        tool = _make_tool(store)
        # Should not raise, just ignore the filter
        result = await tool.execute(start="invalid-date")
        assert isinstance(result, str)

    def test_parse_date_supports_datetime_format(self):
        """_parse_date handles YYYY-MM-DD HH:MM format."""
        tool = RecallTool(MemoryStore(Path("/tmp/fake")))

        # YYYY-MM-DD format
        dt = tool._parse_date("2026-04-21")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 21

        # YYYY-MM-DD HH:MM format
        dt = tool._parse_date("2026-04-21 09:30")
        assert dt is not None
        assert dt.hour == 9
        assert dt.minute == 30

    def test_parse_date_returns_none_for_invalid(self):
        """_parse_date returns None for invalid input."""
        tool = RecallTool(MemoryStore(Path("/tmp/fake")))
        assert tool._parse_date(None) is None
        assert tool._parse_date("") is None
        assert tool._parse_date("not-a-date") is None
