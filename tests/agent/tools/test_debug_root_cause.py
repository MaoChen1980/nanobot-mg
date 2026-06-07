"""Tests for DebugRootCauseTool — root-cause analysis direction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
from nanobot.providers.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_loop(**overrides):
    loop = MagicMock()
    loop.model = "test-model"
    loop.workspace = None
    loop.context = MagicMock()
    loop.context.timezone = "Asia/Shanghai"

    provider = MagicMock()
    provider.chat_stream = AsyncMock(return_value=LLMResponse(content="Try the divide & conquer method"))
    loop.provider = provider

    # sessions mock
    session = MagicMock()
    session.format_history.return_value = [
        {"role": "user", "content": "I get a TypeError"},
        {"role": "assistant", "content": "Let me check", "tool_calls": [{"function": {"name": "read"}}]},
        {"role": "tool", "name": "read", "content": "some result"},
    ]
    sessions = MagicMock()
    sessions.get_or_create.return_value = session
    loop.sessions = sessions

    for k, v in overrides.items():
        setattr(loop, k, v)

    return loop


def _make_tool(loop=None):
    if loop is None:
        loop = _make_mock_loop()
    return DebugRootCauseTool(loop=loop)


def _capture_chat_stream(loop):
    """Capture the messages passed to chat_stream."""
    captured = {}

    async def capture(messages, model=None, **_):
        captured["messages"] = messages
        captured["model"] = model
        return LLMResponse(content="Try divide & conquer")

    loop.provider.chat_stream.side_effect = capture
    return captured


# ---------------------------------------------------------------------------
# execute — basic flow
# ---------------------------------------------------------------------------

class TestExecute:

    @pytest.mark.asyncio
    async def test_returns_advice(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")
        result = await tool.execute(problem="debug this")
        assert result == "Try divide & conquer"

    @pytest.mark.asyncio
    async def test_no_session_returns_error(self):
        tool = _make_tool()
        result = await tool.execute(problem="debug this")
        assert "no active session" in result

    @pytest.mark.asyncio
    async def test_empty_history_returns_error(self):
        loop = _make_mock_loop()
        session = loop.sessions.get_or_create.return_value
        session.format_history.return_value = []
        tool = _make_tool(loop)
        tool.set_context("test-session")
        result = await tool.execute(problem="debug this")
        assert "empty" in result

    @pytest.mark.asyncio
    async def test_includes_methods_in_prompt(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")

        await tool.execute(problem="debug this")
        msg = captured["messages"][0]["content"]
        assert "Divide & Conquer" in msg
        assert "Comparison" in msg
        assert "Rollback" in msg
        assert "Hypothesis Testing" in msg
        assert "Reverse Inference" in msg
        assert "Trial & Error" in msg
        assert "Look Inside" in msg
        assert "Single Variable" in msg

    @pytest.mark.asyncio
    async def test_includes_problem_when_provided(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")

        await tool.execute(problem="TypeError: cannot unpack")
        msg = captured["messages"][0]["content"]
        assert "TypeError: cannot unpack" in msg

    @pytest.mark.asyncio
    async def test_includes_focus_method_when_provided(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")

        await tool.execute(problem="debug this", focus_method="reverse_inference")
        msg = captured["messages"][0]["content"]
        assert "reverse_inference" in msg

    @pytest.mark.asyncio
    async def test_includes_conversation(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")

        await tool.execute(problem="debug this")
        msg = captured["messages"][0]["content"]
        assert "TypeError" in msg

    @pytest.mark.asyncio
    async def test_correct_model(self):
        loop = _make_mock_loop()
        captured = _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("test-session")

        await tool.execute(problem="debug this")
        assert captured["model"] == "test-model"


# ---------------------------------------------------------------------------
# execute — error handling
# ---------------------------------------------------------------------------

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_chat_stream_error_returns_error_msg(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(side_effect=RuntimeError("provider down"))
        tool = _make_tool(loop)
        tool.set_context("test-session")

        result = await tool.execute(problem="debug this")
        assert "Error" in result
        assert "provider down" in result

    @pytest.mark.asyncio
    async def test_empty_response_replaced(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(return_value=LLMResponse(content=""))
        tool = _make_tool(loop)
        tool.set_context("test-session")

        result = await tool.execute(problem="debug this")
        assert result == "(empty response)"

    @pytest.mark.asyncio
    async def test_none_response_replaced(self):
        loop = _make_mock_loop()
        loop.provider.chat_stream = AsyncMock(return_value=LLMResponse(content=None))
        tool = _make_tool(loop)
        tool.set_context("test-session")

        result = await tool.execute(problem="debug this")
        assert result == "(empty response)"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

class TestToolMetadata:

    def test_tool_name(self):
        assert DebugRootCauseTool.name == "debug_root_cause_tool"

    def test_read_only(self):
        assert DebugRootCauseTool.read_only is True

    def test_description_has_purpose(self):
        assert "Purpose" in DebugRootCauseTool.description

    def test_description_differentiates_from_diagnose(self):
        assert "diagnose_tool" in DebugRootCauseTool.description

    def test_description_differentiates_from_assess_me(self):
        assert "assess_me_tool" in DebugRootCauseTool.description


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

    def test_set_context_stores_session_key(self):
        tool = _make_tool()
        tool.set_context("session-abc")
        assert tool._session_key.get() == "session-abc"

    @pytest.mark.asyncio
    async def test_execute_uses_set_context(self):
        loop = _make_mock_loop()
        _capture_chat_stream(loop)
        tool = _make_tool(loop)
        tool.set_context("my-session")

        await tool.execute(problem="debug this")
        loop.sessions.get_or_create.assert_called_with("my-session")
