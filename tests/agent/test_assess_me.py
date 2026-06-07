"""Tests for AssessMe — self-assessment cognition validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.assess_me import (
    _ASSESSMENT_PREFIX,
    assess_me,
    build_assessment_message,
    format_conversation,
    is_assessment_message,
)


# =========================================================================
# format_conversation
# =========================================================================


class TestFormatConversation:
    def test_empty(self) -> None:
        assert format_conversation([]) == ""

    def test_skips_system_role(self) -> None:
        msgs = [
            {"role": "system", "content": "you are a bot"},
            {"role": "user", "content": "hello"},
        ]
        result = format_conversation(msgs)
        assert "[system]" not in result
        assert "[user] hello" in result

    def test_tool_result_truncated(self) -> None:
        long = "x" * 500
        msgs = [{"role": "tool", "name": "read_file", "content": long}]
        result = format_conversation(msgs)
        assert len(result) < 450
        assert "(truncated, 500 chars)" in result

    def test_tool_call_only_assistant_collapsed(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read_file"}},
                    {"function": {"name": "grep"}},
                ],
            }
        ]
        result = format_conversation(msgs)
        assert "[assistant → calls: read_file, grep]" in result

    def test_user_content_list(self) -> None:
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ]
        result = format_conversation(msgs)
        assert "[user] hello" in result

    def test_mixed_conversation(self) -> None:
        msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "read file x"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file"}}]},
            {"role": "tool", "name": "read_file", "content": "file contents"},
            {"role": "assistant", "content": "here it is"},
        ]
        result = format_conversation(msgs)
        assert "[system]" not in result
        assert "[user] read file x" in result
        assert "[assistant → calls: read_file]" in result
        assert "[tool:read_file] file contents" in result
        assert "[assistant] here it is" in result


# =========================================================================
# build_assessment_message
# =========================================================================


class TestBuildAssessmentMessage:
    def test_returns_user_role(self) -> None:
        msg = build_assessment_message("test analysis")
        assert msg["role"] == "user"

    def test_content_prefixed(self) -> None:
        msg = build_assessment_message("test analysis")
        assert msg["content"].startswith(_ASSESSMENT_PREFIX)
        assert "test analysis" in msg["content"]


# =========================================================================
# is_assessment_message
# =========================================================================


class TestIsAssessmentMessage:
    def test_user_role_matches(self) -> None:
        msg = build_assessment_message("some analysis")
        assert is_assessment_message(msg) is True

    def test_tool_role_does_not_match(self) -> None:
        msg = build_assessment_message("some analysis")
        msg["role"] = "tool"
        assert is_assessment_message(msg) is False

    def test_assistant_role_does_not_match(self) -> None:
        msg = build_assessment_message("some analysis")
        msg["role"] = "assistant"
        assert is_assessment_message(msg) is False

    def test_no_prefix_does_not_match(self) -> None:
        msg = {"role": "user", "content": "hello world"}
        assert is_assessment_message(msg) is False

    def test_empty_content_does_not_match(self) -> None:
        msg = {"role": "user", "content": ""}
        assert is_assessment_message(msg) is False

    def test_partial_prefix_does_not_match(self) -> None:
        msg = {"role": "user", "content": "No response needed"}
        assert is_assessment_message(msg) is False

    def test_list_content_does_not_match(self) -> None:
        msg = {"role": "user", "content": [{"type": "text", "text": _ASSESSMENT_PREFIX}]}
        assert is_assessment_message(msg) is False


# =========================================================================
# assess_me (async, with mock provider)
# =========================================================================


class TestAssessMe:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        provider = AsyncMock()
        provider.chat_stream.return_value.content = "**1. What I have done:**\n- Read file x\n- Modified file y"

        result = await assess_me(
            [{"role": "user", "content": "hello"}],
            provider=provider,
            model="test-model",
        )
        assert result is not None
        assert "What I have done" in result
        provider.chat_stream.assert_called_once()
        _, kwargs = provider.chat_stream.call_args
        assert kwargs["model"] == "test-model"
        assert kwargs["max_tokens"] == 1024
        assert kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self) -> None:
        provider = AsyncMock()
        provider.chat_stream.return_value.content = ""

        result = await assess_me(
            [{"role": "user", "content": "hello"}],
            provider=provider,
            model="test-model",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self) -> None:
        provider = AsyncMock()
        provider.chat_stream.side_effect = RuntimeError("LLM down")

        result = await assess_me(
            [{"role": "user", "content": "hello"}],
            provider=provider,
            model="test-model",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_conversation_to_provider(self) -> None:
        provider = AsyncMock()
        provider.chat_stream.return_value.content = "analysis"

        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
        ]
        await assess_me(messages, provider=provider, model="m")

        call_args, _ = provider.chat_stream.call_args
        prompt_messages = call_args[0]
        assert len(prompt_messages) == 1
        assert prompt_messages[0]["role"] == "user"
        assert "first" in prompt_messages[0]["content"]
        assert "response" in prompt_messages[0]["content"]


# =========================================================================
# assess_me_tool
# =========================================================================


class TestAssessMeTool:
    @pytest.mark.asyncio
    async def test_no_session_key_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        loop = MagicMock()
        tool = AssessMeTool(loop=loop)
        result = await tool.execute()
        assert "no active session" in result

    @pytest.mark.asyncio
    async def test_empty_history_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        session = MagicMock()
        session.format_history.return_value = []

        loop = MagicMock()
        loop.sessions.get_or_create.return_value = session

        tool = AssessMeTool(loop=loop)
        tool.set_context(session_key="test-key")
        result = await tool.execute()
        assert "history is empty" in result

    @pytest.mark.asyncio
    async def test_assess_me_called_with_history(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        session = MagicMock()
        session.format_history.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        loop = MagicMock()
        loop.sessions.get_or_create.return_value = session
        loop.provider = MagicMock()
        loop.model = "test-model"

        tool = AssessMeTool(loop=loop)
        tool.set_context(session_key="test-key")

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "**Analysis:** All good"
            result = await tool.execute()

            mock_assess.assert_called_once_with(
                [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
                loop.provider,
                "test-model",
            )
            assert _ASSESSMENT_PREFIX in result
            assert "All good" in result

    @pytest.mark.asyncio
    async def test_focus_prepended_when_provided(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        session = MagicMock()
        session.format_history.return_value = [{"role": "user", "content": "hi"}]

        loop = MagicMock()
        loop.sessions.get_or_create.return_value = session
        loop.provider = MagicMock()
        loop.model = "test-model"

        tool = AssessMeTool(loop=loop)
        tool.set_context(session_key="test-key")

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "some analysis"
            result = await tool.execute(focus="progress")

            assert "Focus: progress" in result

    @pytest.mark.asyncio
    async def test_assess_me_failure_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        session = MagicMock()
        session.format_history.return_value = [{"role": "user", "content": "hi"}]

        loop = MagicMock()
        loop.sessions.get_or_create.return_value = session
        loop.provider = MagicMock()
        loop.model = "test-model"

        tool = AssessMeTool(loop=loop)
        tool.set_context(session_key="test-key")

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = None
            result = await tool.execute()

            assert "Error: assessment LLM call failed" in result


# =========================================================================
# _append_turn_to_session — assessment filtering
# =========================================================================


class TestAppendTurnToSessionAssessmentFilter:
    """Verify that _append_turn_to_session correctly skips assessment messages."""

    def _make_loop(self):
        """Build a mock with the real _append_turn_to_session bound to it."""
        from nanobot.agent.loop import AgentLoop

        loop = MagicMock(spec=AgentLoop)
        loop.max_tool_result_chars = 1000
        loop._sanitize_persisted_blocks = MagicMock(return_value=[])

        # Bind the real _append_turn_to_session method
        loop._append_turn_to_session = (
            AgentLoop._append_turn_to_session.__get__(loop, AgentLoop)
        )
        return loop

    def test_skips_user_assessment(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")
        session.messages = [{"role": "user", "content": "original msg", "timestamp": "2025-01-01T00:00:00"}]

        assessment = build_assessment_message("test analysis")
        assessment["timestamp"] = "2025-01-01T00:01:00"

        messages_to_append = [
            {"role": "assistant", "content": "Let me check...", "timestamp": "2025-01-01T00:02:00"},
            assessment,
            {"role": "assistant", "content": "Here is the answer.", "timestamp": "2025-01-01T00:03:00"},
        ]

        loop._append_turn_to_session(session, messages_to_append, skip=0)

        assert len(session.messages) == 3
        assert session.messages[0]["content"] == "original msg"
        assert session.messages[1]["content"] == "Let me check..."
        assert session.messages[2]["content"] == "Here is the answer."
        assert not any("No response needed" in m.get("content", "") for m in session.messages)

    def test_does_not_skip_tool_result_with_same_prefix(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")
        session.messages = []

        tool_result = {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "assess_me_tool",
            "content": "No response needed, but a reminder:\n\nsome analysis",
            "timestamp": "2025-01-01T00:00:00",
        }

        loop._append_turn_to_session(session, [tool_result], skip=0)

        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "tool"


# =========================================================================
# _make_retry_assess_callback
# =========================================================================


class TestMakeRetryAssessCallback:
    def _make_loop(self):
        """Build a mock with the real _make_retry_assess_callback bound."""
        from nanobot.agent.loop import AgentLoop

        loop = MagicMock(spec=AgentLoop)
        loop.provider = MagicMock()
        loop.model = "test-model"

        loop._make_retry_assess_callback = (
            AgentLoop._make_retry_assess_callback.__get__(loop, AgentLoop)
        )
        return loop

    @pytest.mark.asyncio
    async def test_returns_none_when_session_is_none(self) -> None:
        loop = self._make_loop()
        callback = loop._make_retry_assess_callback(None)
        assert callback is None

    @pytest.mark.asyncio
    async def test_callback_appends_assessment_to_messages(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")

        callback = loop._make_retry_assess_callback(session)
        assert callback is not None

        msgs = [{"role": "user", "content": "hello"}]

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "analysis result"
            result = await callback(msgs)

            assert result is True
            assert len(msgs) == 2
            assert is_assessment_message(msgs[1])

    @pytest.mark.asyncio
    async def test_callback_returns_false_when_assess_fails(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")
        callback = loop._make_retry_assess_callback(session)

        msgs = [{"role": "user", "content": "hello"}]

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = None
            result = await callback(msgs)

            assert result is False
            assert len(msgs) == 1


# =========================================================================
# runner — assess_me_callback in spec
# =========================================================================


class TestRunnerAssessMeSpec:
    def test_assess_me_callback_field_exists(self) -> None:
        from nanobot.agent.runner import AgentRunSpec

        spec = AgentRunSpec(initial_messages=[], tools=MagicMock(), model="m", max_iterations=5, max_tool_result_chars=1000, hook=MagicMock())
        assert hasattr(spec, "assess_me_callback")
        assert spec.assess_me_callback is None

    def test_assess_me_callback_can_be_set(self) -> None:
        from nanobot.agent.runner import AgentRunSpec

        async def dummy(messages):
            return True

        spec = AgentRunSpec(
            initial_messages=[], tools=MagicMock(), model="m", max_iterations=5, max_tool_result_chars=1000, hook=MagicMock(),
            assess_me_callback=dummy,
        )
        assert spec.assess_me_callback is dummy
