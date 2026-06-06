"""Tests for MemorySearchTool and ConversationSearchTool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.agent.tools.conversation_search import ConversationSearchTool


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


# ===========================================================================
# MemorySearchTool tests
# ===========================================================================

class TestMemorySearchToolBasic:
    """Basic MemorySearchTool properties."""

    def test_tool_name(self, tmp_path: Path):
        tool = MemorySearchTool(_make_store(tmp_path))
        assert tool.name == "memory_search_tool"

    def test_tool_description(self, tmp_path: Path):
        tool = MemorySearchTool(_make_store(tmp_path))
        assert "memory_search_tool" in tool.description or "知识库" in tool.description

    def test_tool_is_read_only(self, tmp_path: Path):
        tool = MemorySearchTool(_make_store(tmp_path))
        assert tool.read_only is True

    def test_parameters_have_query_and_k(self, tmp_path: Path):
        tool = MemorySearchTool(_make_store(tmp_path))
        params = tool.parameters
        assert "query" in params["properties"]
        assert "k" in params["properties"]
        assert params["properties"]["k"]["minimum"] == 1
        assert params["properties"]["k"]["maximum"] == 20
        assert "query" in params["required"]


class TestMemorySearchToolKnowledgeMode:
    """MemorySearchTool.execute() behavior."""

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, tmp_path: Path):
        """Empty query should return error message."""
        store = MemoryStore(tmp_path)
        tool = MemorySearchTool(store)
        result = await tool.execute(query="   ")
        assert "provide a query" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_index_returns_no_results(self, tmp_path: Path):
        """Empty memory dir returns no results."""
        store = MemoryStore(tmp_path)
        tool = MemorySearchTool(store)
        result = await tool.execute(query="anything")
        assert "No relevant knowledge found" in result

    @pytest.mark.asyncio
    async def test_with_custom_k(self, tmp_path: Path):
        """k parameter is accepted."""
        store = MemoryStore(tmp_path)
        tool = MemorySearchTool(store)
        result = await tool.execute(query="test", k=3)
        assert isinstance(result, str)


class TestMemorySearchToolDateParsing:
    """_find_line_range edge cases."""

    def test_find_line_range_found(self):
        from nanobot.agent.tools.memory_search import _find_line_range
        text = "line1\nline2\nline3"
        assert _find_line_range(text, "line2") == (2, 2)

    def test_find_line_range_missing(self):
        from nanobot.agent.tools.memory_search import _find_line_range
        assert _find_line_range("abc", "xyz") == (0, 0)

    def test_find_line_range_empty(self):
        from nanobot.agent.tools.memory_search import _find_line_range
        assert _find_line_range("", "") == (0, 0)


# ===========================================================================
# ConversationSearchTool tests
# ===========================================================================

class TestConversationSearchToolBasic:
    """Basic ConversationSearchTool properties."""

    def test_tool_name(self, tmp_path: Path):
        tool = ConversationSearchTool(_make_store(tmp_path))
        assert tool.name == "conversation_search_tool"

    def test_tool_description(self, tmp_path: Path):
        tool = ConversationSearchTool(_make_store(tmp_path))
        assert "conversation_search_tool" in tool.description or "对话" in tool.description

    def test_tool_is_read_only(self, tmp_path: Path):
        tool = ConversationSearchTool(_make_store(tmp_path))
        assert tool.read_only is True

    def test_parameters_have_keyword_and_query(self, tmp_path: Path):
        tool = ConversationSearchTool(_make_store(tmp_path))
        params = tool.parameters
        assert "keyword" in params["properties"]
        assert "query" in params["properties"]
        assert "start" in params["properties"]
        assert "end" in params["properties"]


class TestConversationSearchToolHistoryMode:
    """ConversationSearchTool.execute() behavior."""

    @pytest.mark.asyncio
    async def test_with_keyword_only(self, tmp_path: Path):
        """Keyword matches content."""
        store = _make_store(tmp_path)
        tool = ConversationSearchTool(store)
        result = await tool.execute(keyword="Python")
        assert "Python" in result
        assert "Java" in result

    @pytest.mark.asyncio
    async def test_with_date_range(self, tmp_path: Path):
        """Date range filters history entries."""
        store = _make_store(tmp_path)
        tool = ConversationSearchTool(store)
        today = datetime.now().strftime("%Y-%m-%d")
        result = await tool.execute(keyword="Python", start=today, end=today)
        assert "Python" in result or "No memories found" in result

    @pytest.mark.asyncio
    async def test_query_alias(self, tmp_path: Path):
        """query parameter works as alias for keyword."""
        store = _make_store(tmp_path)
        tool = ConversationSearchTool(store)
        result = await tool.execute(query="Python")
        assert "Python" in result

    @pytest.mark.asyncio
    async def test_no_keyword_provided(self, tmp_path: Path):
        """Neither keyword nor query returns error."""
        store = _make_store(tmp_path)
        tool = ConversationSearchTool(store)
        result = await tool.execute()
        assert "provide" in result.lower() or "Provide" in result


