"""Tests for ReframeTool — problem distillation for focused responses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.reframe import ReframeTool
from nanobot.providers.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_loop(**overrides):
    loop = MagicMock()
    loop.model = "test-model"
    loop.workspace = Path("/fake/workspace")

    # provider mock — chat_stream returns a default ok response
    provider = MagicMock()
    provider.chat_stream = AsyncMock(return_value=LLMResponse(content="advice from model"))
    loop.provider = provider

    for k, v in overrides.items():
        setattr(loop, k, v)

    return loop


def _make_tool(loop=None):
    if loop is None:
        loop = _make_mock_loop()
    return ReframeTool(loop=loop)


def _capture_chat_stream(loop):
    """Return a side-effect fn that captures the messages passed to chat_stream."""
    captured = {}

    async def capture(messages, model=None, **_):
        captured["messages"] = messages
        captured["model"] = model
        return LLMResponse(content="advice from model")

    loop.provider.chat_stream.side_effect = capture
    return captured


# ---------------------------------------------------------------------------
# execute — basic prompt structure
# ---------------------------------------------------------------------------

class TestExecutePromptStructure:

    @pytest.mark.asyncio
    async def test_required_fields_only(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        result = await tool.execute(
            question="How to fix the flaky test?",
            goal="Make CI pass consistently.",
        )

        assert result == "advice from model"
        msg = captured["messages"][0]["content"]

        # Required sections present
        assert "## Goal" in msg
        assert "Make CI pass consistently." in msg
        assert "## Stuck On" in msg
        assert "How to fix the flaky test?" in msg

        # Optional sections omitted
        assert "## What Has Been Tried" not in msg
        assert "## Difficulties / Blockers" not in msg
        assert "## Constraints" not in msg
        assert "## Available Resources" not in msg
        assert "## Focus Area" not in msg

        # Instructions always present
        assert "## Instructions" in msg
        assert "fluff" in msg

    @pytest.mark.asyncio
    async def test_all_fields_filled(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(
            question="How to fix the flaky test?",
            goal="Make CI pass consistently.",
            attempts="Tried retry decorator, tried increasing timeout.",
            difficulties="Test still fails intermittently, no clear pattern.",
            constraints="Must run under 30s, no external services.",
            resources="test_flaky.py, conftest.py, CI logs.",
            focus="debugging",
        )

        msg = captured["messages"][0]["content"]
        assert "## What Has Been Tried" in msg
        assert "Tried retry decorator" in msg
        assert "## Difficulties / Blockers" in msg
        assert "Test still fails intermittently" in msg
        assert "## Constraints" in msg
        assert "Must run under 30s" in msg
        assert "## Available Resources" in msg
        assert "test_flaky.py" in msg
        assert "## Focus Area" in msg
        assert "debugging" in msg

    @pytest.mark.asyncio
    async def test_prompt_starts_with_advisor_context(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        msg = captured["messages"][0]["content"]
        assert msg.startswith("You are acting as an independent advisor.")

    @pytest.mark.asyncio
    async def test_sections_in_correct_order(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(
            question="Q", goal="G",
            attempts="A", difficulties="D",
            constraints="C", resources="R",
            focus="F",
        )
        msg = captured["messages"][0]["content"]
        goal_idx = msg.index("## Goal")
        stuck_idx = msg.index("## Stuck On")
        tried_idx = msg.index("## What Has Been Tried")
        diff_idx = msg.index("## Difficulties / Blockers")
        const_idx = msg.index("## Constraints")
        res_idx = msg.index("## Available Resources")
        focus_idx = msg.index("## Focus Area")
        inst_idx = msg.index("## Instructions")
        assert goal_idx < stuck_idx < tried_idx < diff_idx < const_idx < res_idx < focus_idx < inst_idx

    @pytest.mark.asyncio
    async def test_chat_stream_receives_correct_model(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        assert captured["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_chat_stream_receives_single_user_message(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        assert len(captured["messages"]) == 1
        assert captured["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# execute — project context injection
# ---------------------------------------------------------------------------

class TestExecuteProjectContext:

    @pytest.mark.asyncio
    async def test_workspace_included_when_available(self):
        loop = _make_mock_loop()
        loop.workspace = Path("/some/project")
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        msg = captured["messages"][0]["content"]
        assert "## Project Context" in msg
        assert "some" in msg and "project" in msg

    @pytest.mark.asyncio
    async def test_workspace_omitted_when_not_available(self):
        loop = _make_mock_loop()
        loop.workspace = None
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        msg = captured["messages"][0]["content"]
        assert "## Project Context" not in msg

    @pytest.mark.asyncio
    async def test_project_card_loaded_when_exists(self, tmp_path):
        loop = _make_mock_loop()
        loop.workspace = tmp_path
        (tmp_path / "project_card.md").write_text("Project: My App\nGoal: Ship v2")
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        msg = captured["messages"][0]["content"]
        assert "Project: My App" in msg
        assert "Goal: Ship v2" in msg

    @pytest.mark.asyncio
    async def test_project_card_omitted_when_missing(self, tmp_path):
        loop = _make_mock_loop()
        loop.workspace = tmp_path
        # no project_card.md
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")
        msg = captured["messages"][0]["content"]
        assert "## Project Context" in msg
        # but no card content beyond the directory line

    @pytest.mark.asyncio
    async def test_project_card_read_error_does_not_crash(self, tmp_path):
        loop = _make_mock_loop()
        loop.workspace = tmp_path
        # Create a project_card that can't be read (e.g. a directory)
        (tmp_path / "project_card.md").mkdir()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)

        await tool.execute(question="Q", goal="G")  # should not raise


# ---------------------------------------------------------------------------
# execute — error handling
# ---------------------------------------------------------------------------

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_chat_stream_error_returns_error_message(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(side_effect=ValueError("connection lost"))
        tool = _make_tool(loop)

        result = await tool.execute(question="Q", goal="G")
        assert "Error" in result
        assert "connection lost" in result

    @pytest.mark.asyncio
    async def test_empty_response_replaced_with_placeholder(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(return_value=LLMResponse(content=""))
        tool = _make_tool(loop)

        result = await tool.execute(question="Q", goal="G")
        assert result == "(empty response)"

    @pytest.mark.asyncio
    async def test_whitespace_only_response_stripped(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(return_value=LLMResponse(content="   \n  "))
        tool = _make_tool(loop)

        result = await tool.execute(question="Q", goal="G")
        assert result == "(empty response)"

    @pytest.mark.asyncio
    async def test_none_response_replaced_with_placeholder(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(return_value=LLMResponse(content=None))
        tool = _make_tool(loop)

        result = await tool.execute(question="Q", goal="G")
        assert result == "(empty response)"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

class TestToolMetadata:

    def test_tool_name(self):
        assert ReframeTool.name == "reframe_tool"

    def test_read_only(self):
        assert ReframeTool.read_only is True

    def test_description_includes_purpose(self):
        assert "Purpose" in ReframeTool.description

    def test_description_includes_when_to_use(self):
        assert "When to use" in ReframeTool.description

    def test_description_includes_what_to_provide(self):
        assert "What to provide" in ReframeTool.description


# ---------------------------------------------------------------------------
# Parameter schema
# ---------------------------------------------------------------------------

class TestParameterSchema:

    def test_question_is_required(self):
        assert "question" in ReframeTool._tool_parameters_schema["required"]

    def test_goal_is_required(self):
        assert "goal" in ReframeTool._tool_parameters_schema["required"]

    def test_optional_fields_not_required(self):
        for field in ("attempts", "difficulties", "constraints", "resources", "focus"):
            assert field not in ReframeTool._tool_parameters_schema["required"]

    def test_all_params_have_descriptions(self):
        props = ReframeTool._tool_parameters_schema["properties"]
        for name in ("question", "goal", "attempts", "difficulties", "constraints", "resources", "focus"):
            assert name in props
            assert "description" in props[name]
