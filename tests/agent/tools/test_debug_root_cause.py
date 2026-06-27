"""Tests for DebugRootCauseTool — root-cause analysis direction."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
from nanobot.providers.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_MESSAGES = [
    {"role": "user", "content": "I get a TypeError"},
    {"role": "assistant", "content": "Let me check"},
    {"role": "tool", "name": "read", "content": "some result"},
]


def _make_tool():
    return DebugRootCauseTool()


# ---------------------------------------------------------------------------
# execute — basic flow
# ---------------------------------------------------------------------------


class TestExecute:

    @pytest.mark.asyncio
    async def test_returns_advice(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)
        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="Try divide & conquer")
            result = await tool.execute(problem="debug this")
        assert result == "Try divide & conquer"

    @pytest.mark.asyncio
    async def test_no_messages_returns_error(self):
        tool = _make_tool()
        result = await tool.execute(problem="debug this")
        assert "no active session" in result

    @pytest.mark.asyncio
    async def test_includes_methods_in_prompt(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(problem="debug this")

        msg = mock_chat.call_args[0][0][0]["content"]
        assert "Divide & Conquer" in msg
        assert "Comparison" in msg
        assert "Rollback" in msg
        assert "Hypothesis Testing" in msg
        assert "Reverse Inference" in msg
        assert "Trial & Error" in msg
        assert "Look Inside" in msg
        assert "Single Variable" in msg
        assert "Boundary Testing" in msg
        assert "Reproduction" in msg
        assert "Elimination" in msg
        assert "Substitution" in msg
        assert "Stack Trace" in msg
        assert "Log Injection" in msg
        assert "Time Travel" in msg
        assert "Wait & Observe" in msg
        assert "Layer Stripping" in msg
        assert "Outlier Analysis" in msg
        assert "Force Failure" in msg
        assert "Peer Review" in msg

    @pytest.mark.asyncio
    async def test_includes_problem_when_provided(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(problem="TypeError: cannot unpack")

        msg = mock_chat.call_args[0][0][0]["content"]
        assert "TypeError: cannot unpack" in msg

    @pytest.mark.asyncio
    async def test_includes_focus_method_when_provided(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(problem="debug this", focus_method="reverse_inference")

        msg = mock_chat.call_args[0][0][0]["content"]
        assert "reverse_inference" in msg

    @pytest.mark.asyncio
    async def test_includes_conversation(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(problem="debug this")

        msg = mock_chat.call_args[0][0][0]["content"]
        assert "TypeError" in msg

    @pytest.mark.asyncio
    async def test_sends_single_user_message(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="advice")
            await tool.execute(problem="debug this")

        mock_chat.assert_called_once()
        msgs = mock_chat.call_args[0][0]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ---------------------------------------------------------------------------
# execute — error handling
# ---------------------------------------------------------------------------

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_chat_stream_error_returns_error_msg(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.side_effect = RuntimeError("provider down")
            result = await tool.execute(problem="debug this")

        assert "Error" in result
        assert "provider down" in result

    @pytest.mark.asyncio
    async def test_empty_response_replaced(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="")
            result = await tool.execute(problem="debug this")
        assert result == "问题太难，目前没有结论"

    @pytest.mark.asyncio
    async def test_none_response_replaced(self):
        tool = _make_tool()
        tool.set_context(messages=_SAMPLE_MESSAGES)

        with patch("nanobot.agent.tools.debug_root_cause.chat_stream_with_retry", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content=None)
            result = await tool.execute(problem="debug this")
        assert result == "问题太难，目前没有结论"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

class TestToolMetadata:

    def test_tool_name(self):
        assert DebugRootCauseTool.name == "debug_root_cause"

    def test_read_only(self):
        assert DebugRootCauseTool.read_only is True

    def test_description_has_purpose(self):
        assert "Purpose" in DebugRootCauseTool.description

    def test_description_differentiates_from_assess_me(self):
        assert "assess_me" in DebugRootCauseTool.description


# ---------------------------------------------------------------------------
# Parameter schema
# ---------------------------------------------------------------------------

class TestParameterSchema:

    def test_problem_is_required(self):
        required = DebugRootCauseTool._tool_parameters_schema.get("required", [])
        assert required == ["problem"]

    def test_problem_has_description(self):
        props = DebugRootCauseTool._tool_parameters_schema["properties"]
        assert "description" in props["problem"]

    def test_focus_method_has_description(self):
        props = DebugRootCauseTool._tool_parameters_schema["properties"]
        assert "description" in props["focus_method"]


# ---------------------------------------------------------------------------
# set_context
# ---------------------------------------------------------------------------

class TestSetContext:

    def test_set_context_stores_messages(self):
        tool = _make_tool()
        msgs = [{"role": "user", "content": "hello"}]
        tool.set_context(messages=msgs)
        assert tool._messages.get() == msgs
