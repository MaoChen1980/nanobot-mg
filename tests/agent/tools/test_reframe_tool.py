"""Tests for ReframeTool — problem distillation for focused responses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.tools.reframe import ReframeTool
from nanobot.providers.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(workspace: Path | None = None):
    return ReframeTool(workspace=workspace)


# ---------------------------------------------------------------------------
# execute — basic prompt structure
# ---------------------------------------------------------------------------

class TestExecutePromptStructure:

    @pytest.mark.asyncio
    async def test_required_fields_only(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice from model")
            result = await tool.execute(
                question="How to fix the flaky test?",
                goal="Make CI pass consistently.",
            )
            msg = mock_chat.call_args[0][0][0]["content"]

        assert result == "advice from model"

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
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(
                question="How to fix the flaky test?",
                goal="Make CI pass consistently.",
                attempts="Tried retry decorator, tried increasing timeout.",
                difficulties="Test still fails intermittently, no clear pattern.",
                constraints="Must run under 30s, no external services.",
                resources="test_flaky.py, conftest.py, CI logs.",
                focus="debugging",
            )
            msg = mock_chat.call_args[0][0][0]["content"]

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
    async def test_prompt_starts_with_context_section(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msg = mock_chat.call_args[0][0][0]["content"]
        assert msg.startswith("You are acting as an independent advisor")

    @pytest.mark.asyncio
    async def test_sections_in_correct_order(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(
                question="Q", goal="G",
                attempts="A", difficulties="D",
                constraints="C", resources="R",
                focus="F",
            )
            msg = mock_chat.call_args[0][0][0]["content"]

        goal_idx = msg.index("## Goal")
        stuck_idx = msg.index("## Stuck On")
        tried_idx = msg.index("## What Has Been Tried")
        diff_idx = msg.index("## Difficulties / Blockers")
        const_idx = msg.index("## Constraints")
        res_idx = msg.index("## Available Resources")
        focus_idx = msg.index("## Focus Area")
        instr_idx = msg.index("## Instructions")
        assert goal_idx < stuck_idx < tried_idx < diff_idx < const_idx < res_idx < focus_idx < instr_idx

    @pytest.mark.asyncio
    async def test_chat_receives_single_user_message(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msgs = mock_chat.call_args[0][0]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ---------------------------------------------------------------------------
# execute — project context injection
# ---------------------------------------------------------------------------

class TestExecuteProjectContext:

    @pytest.mark.asyncio
    async def test_workspace_included_when_available(self):
        tool = _make_tool(workspace=Path("/some/project"))
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msg = mock_chat.call_args[0][0][0]["content"]
        assert "## Project Context" in msg
        assert "some" in msg and "project" in msg

    @pytest.mark.asyncio
    async def test_workspace_omitted_when_not_available(self):
        tool = _make_tool()  # workspace=None
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msg = mock_chat.call_args[0][0][0]["content"]
        assert "## Project Context" not in msg

    @pytest.mark.asyncio
    async def test_project_card_loaded_when_exists(self, tmp_path):
        tool = _make_tool(workspace=tmp_path)
        (tmp_path / "project_card.md").write_text("Project: My App\nGoal: Ship v2")
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msg = mock_chat.call_args[0][0][0]["content"]
        assert "Project: My App" in msg
        assert "Goal: Ship v2" in msg

    @pytest.mark.asyncio
    async def test_project_card_omitted_when_missing(self, tmp_path):
        tool = _make_tool(workspace=tmp_path)
        # no project_card.md
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")
            msg = mock_chat.call_args[0][0][0]["content"]
        assert "## Project Context" in msg
        # but no card content beyond the directory line

    @pytest.mark.asyncio
    async def test_project_card_read_error_does_not_crash(self, tmp_path):
        tool = _make_tool(workspace=tmp_path)
        # Create a project_card that can't be read (e.g. a directory)
        (tmp_path / "project_card.md").mkdir()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(question="Q", goal="G")  # should not raise


# ---------------------------------------------------------------------------
# execute — error handling
# ---------------------------------------------------------------------------

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_chat_error_returns_error_message(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.side_effect = ValueError("connection lost")
            result = await tool.execute(question="Q", goal="G")
        assert "Error" in result
        assert "connection lost" in result

    @pytest.mark.asyncio
    async def test_empty_response_replaced_with_placeholder(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="")
            result = await tool.execute(question="Q", goal="G")
        assert result == "问题太难，目前没有结论"

    @pytest.mark.asyncio
    async def test_whitespace_only_response_stripped(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="   \n  ")
            result = await tool.execute(question="Q", goal="G")
        assert result == "问题太难，目前没有结论"

    @pytest.mark.asyncio
    async def test_none_response_replaced_with_placeholder(self):
        tool = _make_tool()
        with patch("nanobot.agent.tools.reframe.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content=None)
            result = await tool.execute(question="Q", goal="G")
        assert result == "问题太难，目前没有结论"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

class TestToolMetadata:

    def test_tool_name(self):
        assert ReframeTool.name == "reframe"

    def test_read_only(self):
        assert ReframeTool.read_only is True

    def test_description_includes_purpose(self):
        assert "Purpose" in ReframeTool.description

    def test_description_includes_when_to_use(self):
        assert "When to call" in ReframeTool.description

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
