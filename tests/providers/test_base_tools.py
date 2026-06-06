"""Tests for LLMProvider tool-related helpers (_tool_name, _tool_cache_marker_indices, etc.)."""

from __future__ import annotations

from nanobot.providers.base import GenerationSettings, LLMProvider, ToolCallRequest


class TestToolName:
    def test_openai_style(self):
        """OpenAI-style tool schema uses 'name' key."""
        tool = {"name": "web_search", "description": "Search the web"}
        assert LLMProvider._tool_name(tool) == "web_search"

    def test_anthropic_style(self):
        """Anthropic-style tool schema uses 'function' dict."""
        tool = {"function": {"name": "calculator"}}
        assert LLMProvider._tool_name(tool) == "calculator"

    def test_no_name(self):
        """No matching name returns empty string."""
        tool = {"description": "A tool without a name"}
        assert LLMProvider._tool_name(tool) == ""


class TestToolCacheMarkerIndices:
    def test_empty_tools(self):
        assert LLMProvider._tool_cache_marker_indices([]) == []

    def test_single_tool(self):
        tools = [{"name": "web_search"}]
        assert LLMProvider._tool_cache_marker_indices(tools) == [0]

    def test_all_builtin(self):
        tools = [{"name": "web_search"}, {"name": "read_file"}]
        assert LLMProvider._tool_cache_marker_indices(tools) == [1]

    def test_builtin_and_mcp(self):
        tools = [{"name": "web_search"}, {"name": "mcp_filesystem"}, {"name": "mcp_github"}]
        result = LLMProvider._tool_cache_marker_indices(tools)
        assert result == [0, 2]

    def test_all_mcp(self):
        tools = [{"name": "mcp_filesystem"}, {"name": "mcp_github"}]
        result = LLMProvider._tool_cache_marker_indices(tools)
        assert result == [1]

    def test_dedup_when_last_builtin_equals_tail(self):
        tools = [{"name": "web_search"}]
        result = LLMProvider._tool_cache_marker_indices(tools)
        assert result == [0]


class TestToolCallRequestToOpenAI:
    def test_basic(self):
        tc = ToolCallRequest(id="call_1", name="web_search", arguments={"q": "hello"})
        result = tc.to_openai_tool_call()
        assert result["id"] == "call_1"
        assert result["type"] == "function"
        assert result["function"]["name"] == "web_search"
        assert "arguments" in result["function"]

    def test_with_extra_content(self):
        tc = ToolCallRequest(id="call_1", name="web_search", arguments={}, extra_content={"meta": "data"})
        result = tc.to_openai_tool_call()
        assert result["extra_content"] == {"meta": "data"}

    def test_with_provider_specific_fields(self):
        tc = ToolCallRequest(id="call_1", name="web_search", arguments={},
                             provider_specific_fields={"cache_control": {"type": "ephemeral"}})
        result = tc.to_openai_tool_call()
        assert result["provider_specific_fields"] == {"cache_control": {"type": "ephemeral"}}

    def test_with_function_provider_specific_fields(self):
        tc = ToolCallRequest(id="call_1", name="web_search", arguments={},
                             function_provider_specific_fields={"cache_control": {"type": "ephemeral"}})
        result = tc.to_openai_tool_call()
        assert result["function"]["provider_specific_fields"] == {"cache_control": {"type": "ephemeral"}}


class TestGenerationSettings:
    def test_defaults(self):
        gs = GenerationSettings()
        assert gs.temperature == 0.7
        assert gs.max_tokens == 4096
        assert gs.reasoning_effort is None

    def test_frozen(self):
        gs = GenerationSettings()
        import dataclasses
        assert dataclasses.fields(gs)
