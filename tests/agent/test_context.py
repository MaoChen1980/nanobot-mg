"""Tests for ContextBuilder — framework search injection and system prompt assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_RESULT = {
    "source": "framework/workflows/test.md",
    "heading": "Test Workflow",
    "text": "This is the matched workflow content for testing purposes.",
    "score": 0.65,
}


def _make_builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(_make_workspace(tmp_path))


# ---------------------------------------------------------------------------
# _build_framework_search_section — query extraction
# ---------------------------------------------------------------------------


def test_search_extracts_from_last_assistant(tmp_path):
    """Last assistant message has content → query extracted and section generated."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差→给建议", "tool_calls": [{"function": {"name": "get_weather"}}]},
        {"role": "tool", "content": "25°", "tool_call_id": "call_1", "name": "get_weather"},
        {"role": "assistant", "content": "深圳25度,北京20度,温差明显"},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]):
        result = builder._build_framework_search_section(history)

    assert "Relevant Framework Docs" in result
    assert "Test Workflow" in result


def test_search_skips_when_last_message_is_user(tmp_path):
    """History ends with user message → no prior turn, returns empty."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮答案"},
        {"role": "user", "content": "当前问题"},
    ]
    with patch.object(builder.memory.framework_index, "search") as mock_search:
        result = builder._build_framework_search_section(history)

    assert result == ""
    mock_search.assert_not_called()


def test_search_empty_history(tmp_path):
    """Empty history (first turn) → returns empty."""
    builder = _make_builder(tmp_path)
    with patch.object(builder.memory.framework_index, "search") as mock_search:
        result = builder._build_framework_search_section([])

    assert result == ""
    mock_search.assert_not_called()


def test_search_no_assistant_in_history(tmp_path):
    """History has only user messages → no assistant to extract from."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "问题1"},
        {"role": "user", "content": "问题2"},
    ]
    with patch.object(builder.memory.framework_index, "search") as mock_search:
        result = builder._build_framework_search_section(history)

    assert result == ""
    mock_search.assert_not_called()


def test_search_skips_empty_assistant_content(tmp_path):
    """Last assistant has empty content → skip back to previous assistant that has content."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差→给建议", "tool_calls": [{"function": {"name": "get_weather"}}]},
        {"role": "tool", "content": "25°", "tool_call_id": "call_1", "name": "get_weather"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "message_tool", "arguments": '{"content": "深圳25°"}'}}]},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]) as mock_search:
        result = builder._build_framework_search_section(history)

    assert "Relevant Framework Docs" in result
    args, kwargs = mock_search.call_args
    query = kwargs.get("query", args[0] if args else "")
    assert "查天气" in query


def test_search_extracts_from_message_tool(tmp_path):
    """Content inside message_tool() tool call arguments → extracted as query."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "比较温度"},
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "message_tool", "arguments": '{"content": "比较温度→给出建议→得出结果"}'}},
         ]},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]) as mock_search:
        result = builder._build_framework_search_section(history)

    assert "Relevant Framework Docs" in result
    args, kwargs = mock_search.call_args
    query = kwargs.get("query", args[0] if args else "")
    assert "比较温度" in query


def test_search_prefers_content_over_message_tool(tmp_path):
    """When assistant has both text content and tool_calls → text content is used."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant",
         "content": "意图：查天气→比较温差",
         "tool_calls": [
             {"function": {"name": "message_tool", "arguments": '{"content": "NOT THIS"}'}},
         ]},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]) as mock_search:
        builder._build_framework_search_section(history)

    args, kwargs = mock_search.call_args
    query = kwargs.get("query", args[0] if args else "")
    assert "查天气" in query
    assert "NOT THIS" not in query


def test_search_short_content_ignored(tmp_path):
    """Content shorter than 10 chars → insufficient, returns empty."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    with patch.object(builder.memory.framework_index, "search") as mock_search:
        result = builder._build_framework_search_section(history)

    assert result == ""
    mock_search.assert_not_called()


def test_search_no_matching_results(tmp_path):
    """Search returns empty list → no section generated."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差"},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[]):
        result = builder._build_framework_search_section(history)

    assert result == ""


def test_search_first_after_user_is_used(tmp_path):
    """Multiple assistant messages in one turn → first after user (intent) is used, not last."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差→给建议", "tool_calls": [{"function": {"name": "get_weather"}}]},
        {"role": "tool", "content": "25°", "tool_call_id": "call_1", "name": "get_weather"},
        {"role": "assistant", "content": "工具获取中"},
        {"role": "tool", "content": "20°", "tool_call_id": "call_2", "name": "get_weather"},
        {"role": "assistant", "content": "北京20度,天津25度"},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]) as mock_search:
        builder._build_framework_search_section(history)

    args, kwargs = mock_search.call_args
    query = kwargs.get("query", args[0] if args else "")
    assert "查天气→比较温差→给建议" in query
    assert "北京20度" not in query


def test_search_message_tool_parsed_args(tmp_path):
    """message_tool arguments already parsed as dict (not JSON string)."""
    builder = _make_builder(tmp_path)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "message_tool", "arguments": {"content": "查天气→比较→得出结论"}}},
         ]},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]) as mock_search:
        builder._build_framework_search_section(history)

    args, kwargs = mock_search.call_args
    query = kwargs.get("query", args[0] if args else "")
    assert "查天气→比较→得出结论" in query


# ---------------------------------------------------------------------------
# Integration: system prompt template rendering
# ---------------------------------------------------------------------------


def test_system_prompt_includes_framework_search(tmp_path):
    """build_system_prompt renders framework_search section when passed."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt(framework_search="## Relevant Framework Docs\n\nTest content here.")

    assert "## Relevant Framework Docs" in prompt
    assert "Test content here" in prompt


def test_system_prompt_omits_framework_search_when_none(tmp_path):
    """build_system_prompt omits framework_search section when None."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "Relevant Framework Docs" not in prompt


def test_system_prompt_omits_framework_search_when_empty(tmp_path):
    """build_system_prompt omits framework_search section when empty string."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt(framework_search="")

    assert "Relevant Framework Docs" not in prompt


# ---------------------------------------------------------------------------
# Integration: full pipeline via build_messages
# ---------------------------------------------------------------------------


def test_build_messages_includes_search_when_results(tmp_path):
    """build_messages includes Relevant Framework Docs when search returns results."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差→给建议", "tool_calls": [{"function": {"name": "get_weather"}}]},
        {"role": "tool", "content": "25°", "tool_call_id": "call_1", "name": "get_weather"},
        {"role": "assistant", "content": "深圳25度,北京20度,温差明显"},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[_MOCK_RESULT]):
        messages = builder.build_messages(history=history, current_message="然后呢")

    system_content = messages[0]["content"]
    assert "Relevant Framework Docs" in system_content
    assert "Test Workflow" in system_content


def test_build_messages_omits_search_when_no_results(tmp_path):
    """build_messages skips Relevant Framework Docs when search returns nothing."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    history = [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查天气→比较温差"},
    ]
    with patch.object(builder.memory.framework_index, "search", return_value=[]):
        messages = builder.build_messages(history=history, current_message="然后呢")

    system_content = messages[0]["content"]
    assert "Relevant Framework Docs" not in system_content


def test_build_messages_first_turn_no_search(tmp_path):
    """First turn (no history) → no framework search, no section."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    with patch.object(builder.memory.framework_index, "search") as mock_search:
        messages = builder.build_messages(history=[], current_message="你好")

    system_content = messages[0]["content"]
    assert "Relevant Framework Docs" not in system_content
    mock_search.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: existing behavior unchanged
# ---------------------------------------------------------------------------


def test_lessons_never_injected(tmp_path) -> None:
    """Past Lessons should never appear in system prompt, even if file exists."""
    workspace = _make_workspace(tmp_path)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "lessons.md").write_text(
        "- Always check error codes\n- Use async/await consistently\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt


def test_lessons_omitted_when_file_missing(tmp_path) -> None:
    """Sanity check: no Past Lessons when file doesn't exist."""
    workspace = _make_workspace(tmp_path)

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt

