"""Tests for AssessMeTool — cognitive assessment direction."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.tools.assess_me import AssessMeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_MESSAGES = [
    {"role": "user", "content": "I get a TypeError"},
    {"role": "assistant", "content": "Let me check"},
    {"role": "tool", "name": "read", "content": "some result"},
]


def _make_tool():
    return AssessMeTool()


# ---------------------------------------------------------------------------
# execute — basic flow
# ---------------------------------------------------------------------------


class TestExecute:

    @pytest.mark.asyncio
    async def test_returns_assessment(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)
        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "You have verified the input"
            result = await tool.execute()
        assert "You have verified the input" in result

    @pytest.mark.asyncio
    async def test_no_messages_returns_error(self):
        tool = _make_tool()
        result = await tool.execute()
        assert "no active session" in result

    @pytest.mark.asyncio
    async def test_passes_verify_to_assess_me(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "verified"
            await tool.execute(verify="The file exists, The API returns JSON")

        mock_assess.assert_called_once()
        _, kwargs = mock_assess.call_args
        assert kwargs["verify"] == "The file exists, The API returns JSON"

    @pytest.mark.asyncio
    async def test_includes_focus_in_output(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "Here is the assessment"
            result = await tool.execute(focus="assumptions")

        assert "Focus: assumptions" in result
        assert "Here is the assessment" in result

    @pytest.mark.asyncio
    async def test_sends_messages_to_assess_me(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "ok"
            await tool.execute()

        mock_assess.assert_called_once()
        args, _ = mock_assess.call_args
        assert args[0] == _SAMPLE_MESSAGES

    @pytest.mark.asyncio
    async def test_none_response_replaced(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = None
            result = await tool.execute()
        assert "Error" in result
        assert "empty response" in result


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:

    def test_tool_name(self):
        assert AssessMeTool.name == "assess_me"

    def test_read_only(self):
        assert AssessMeTool.read_only is True

    def test_description_has_purpose(self):
        assert "separate LLM" in AssessMeTool.description


# ---------------------------------------------------------------------------
# Parameter schema
# ---------------------------------------------------------------------------


class TestParameterSchema:

    def test_no_required_parameters(self):
        required = AssessMeTool._tool_parameters_schema.get("required", [])
        assert required == []

    def test_focus_has_description(self):
        props = AssessMeTool._tool_parameters_schema["properties"]
        assert "description" in props["focus"]

    def test_verify_has_description(self):
        props = AssessMeTool._tool_parameters_schema["properties"]
        assert "description" in props["verify"]


# ---------------------------------------------------------------------------
# set_context
# ---------------------------------------------------------------------------


class TestSetContext:

    def test_set_context_stores_messages(self):
        tool = _make_tool()
        msgs = [{"role": "user", "content": "hello"}]
        tool.set_context(messages=msgs)
        assert tool._messages.get() == msgs
