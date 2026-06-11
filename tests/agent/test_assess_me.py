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

    def test_user_content_truncated(self) -> None:
        """Long user content is truncated like tool results."""
        long = "x" * 500
        msgs = [{"role": "user", "content": long}]
        result = format_conversation(msgs)
        assert len(result) < 450
        assert "(truncated, 500 chars)" in result


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

    def test_no_tailing_directive(self) -> None:
        """Verify the injected message has no trailing instruction.

        The assessment should be purely informational — no call to action
        appended after the [/assess] marker.
        """
        msg = build_assessment_message("some analysis")
        content = msg["content"]
        # Should end with [/assess], not with any directive text
        assert content.rstrip().endswith("[/assess]")
        # Assert the old Chinese directive is gone
        assert "请继续按计划推进" not in content


# =========================================================================
# _maybe_assess — interval trigger logic (assistant_count)
# =========================================================================


class TestMaybeAssess:
    """Test _maybe_assess triggers correctly with assistant_count.

    The interval trigger should fire based on LLM assistant turns, not user
    messages — so dense tool-call sequences get periodic direction checks.
    """

    def _make_handler(self):
        from nanobot.agent.loop import AgentLoop
        from nanobot.agent.loop_message_handlers import UserMessageHandler

        loop = MagicMock(spec=AgentLoop)
        return UserMessageHandler(loop)

    def _make_msgs(self, n_assistants: int) -> list[dict]:
        """Build alternating user/assistant messages with *n_assistants* assistant turns."""
        msgs: list[dict] = []
        for i in range(n_assistants):
            msgs.append({"role": "user", "content": f"msg {i}"})
            msgs.append({"role": "assistant", "content": f"resp {i}"})
        return msgs

    @pytest.mark.asyncio
    async def test_no_trigger_when_few_assistants(self) -> None:
        """assistant_count < interval (10) → no trigger."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(4)  # 4 assistant, 8 total
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_on_first_turn(self) -> None:
        """assistant_count == 0 → no trigger (clean session)."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = []
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_at_interval(self) -> None:
        """assistant_count % 10 == 0 → trigger assess_me."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(10)  # 10 assistant, 20 total
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "analysis at 10"
            await handler._maybe_assess(session, history)

            mock_assess.assert_called_once()
            # Verify assessment was injected into history
            assert any(
                m.get("role") == "user" and "[assess]" in m.get("content", "")
                for m in history
            )

    @pytest.mark.asyncio
    async def test_trigger_at_multiple_of_interval(self) -> None:
        """assistant_count == 20 also triggers (next multiple)."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(20)
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "analysis at 20"
            await handler._maybe_assess(session, history)

            mock_assess.assert_called_once()
            assert any(
                m.get("role") == "user" and "[assess]" in m.get("content", "")
                for m in history
            )

    @pytest.mark.asyncio
    async def test_no_trigger_just_past_interval(self) -> None:
        """assistant_count == 11 (just past 10, 11%10=1) → no trigger."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(11)
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_compress_triggers_regardless_of_count(self) -> None:
        """compress_triggered=True fires even with 0 assistants."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = []
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "post-compress analysis"
            await handler._maybe_assess(session, history, compress_triggered=True)

            mock_assess.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_inject_when_assess_empty(self) -> None:
        """Empty assess_me result → history unchanged."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(10)
        history = session.format_history()
        original_len = len(history)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = ""
            await handler._maybe_assess(session, history)

            assert len(history) == original_len, "history should not grow on empty result"

    @pytest.mark.asyncio
    async def test_handles_assess_exception_gracefully(self) -> None:
        """Exception in assess_me → handled, no crash, no injection."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.messages = self._make_msgs(10)
        history = session.format_history()
        original_len = len(history)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.side_effect = RuntimeError("LLM down")
            await handler._maybe_assess(session, history)

            assert len(history) == original_len, "history unchanged after exception"

    @pytest.mark.asyncio
    async def test_only_assistant_count_matters_not_user_count(self) -> None:
        """Many user messages but few assistant → no trigger.

        This is the key behavioral change: dense user messages without
        corresponding LLM turns should not trigger assess_me.
        """
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        # 15 user messages but only 5 assistant responses
        msgs: list[dict] = []
        for i in range(15):
            msgs.append({"role": "user", "content": f"msg {i}"})
        for i in range(5):
            msgs.append({"role": "assistant", "content": f"resp {i}"})
        session.messages = msgs
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()


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
        msg = {"role": "user", "content": "[ass"}
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
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = "**1. What I have done:**\n- Read file x\n- Modified file y"

            result = await assess_me(
                [{"role": "user", "content": "hello"}],
            )
            assert result is not None
            assert "What I have done" in result
            mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self) -> None:
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = ""

            result = await assess_me(
                [{"role": "user", "content": "hello"}],
            )
            assert result == ""

    @pytest.mark.asyncio
    async def test_llm_exception_propagates(self) -> None:
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = RuntimeError("LLM down")

            with pytest.raises(RuntimeError, match="LLM down"):
                await assess_me(
                    [{"role": "user", "content": "hello"}],
                )

    @pytest.mark.asyncio
    async def test_passes_conversation_to_provider(self) -> None:
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = "analysis"

            messages = [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "response"},
            ]
            await assess_me(messages)

            call_args, _ = mock_fn.call_args
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
    async def test_no_messages_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        result = await tool.execute()
        assert "no active session" in result

    @pytest.mark.asyncio
    async def test_assess_me_called_with_messages(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        tool.set_context(messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "**Analysis:** All good"
            result = await tool.execute()

            mock_assess.assert_called_once_with(
                [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
                verify="",
            )
            assert "All good" in result

    @pytest.mark.asyncio
    async def test_verify_passed_through(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        tool.set_context(messages=[
            {"role": "user", "content": "Check these claims"},
        ])

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "✅ Verified: config exists\n❌ Not verified: port 8080"
            result = await tool.execute(verify="Config at /etc/app/config.yml, Port 8080 is open")

            mock_assess.assert_called_once_with(
                [{"role": "user", "content": "Check these claims"}],
                verify="Config at /etc/app/config.yml, Port 8080 is open",
            )
            assert "Verified" in result

    @pytest.mark.asyncio
    async def test_focus_prepended_when_provided(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        tool.set_context(messages=[{"role": "user", "content": "hi"}])

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "some analysis"
            result = await tool.execute(focus="progress")

            assert "Focus: progress" in result

    @pytest.mark.asyncio
    async def test_assess_me_failure_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        tool.set_context(messages=[{"role": "user", "content": "hi"}])

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = ""
            result = await tool.execute()

            assert "Error: assessment LLM returned empty response" in result

    @pytest.mark.asyncio
    async def test_assess_me_exception_returns_error(self) -> None:
        from nanobot.agent.tools.assess_me_tool import AssessMeTool

        tool = AssessMeTool()
        tool.set_context(messages=[{"role": "user", "content": "hi"}])

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.side_effect = RuntimeError("LLM down")
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
        assert not any("[assess]" in m.get("content", "") for m in session.messages)

    def test_does_not_skip_tool_result_with_same_prefix(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")
        session.messages = []

        tool_result = {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "assess_me_tool",
            "content": "some analysis",
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
            mock_assess.return_value = ""
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


# =========================================================================
# assess_me.md template — field coverage
# =========================================================================


class TestAssessMeTemplate:
    """Verify the assess_me.md template includes the new Thinking patterns field."""

    def test_includes_thinking_patterns(self) -> None:
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/assess_me.md",
            conversation="user: hello\nassistant: hi",
            verify="",
        )
        assert "Thinking patterns" in content, (
            "Template should include the Thinking patterns field"
        )

    def test_renders_without_verify(self) -> None:
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/assess_me.md",
            conversation="user: hello",
        )
        assert "Thinking patterns" in content
        assert "Items to Verify" not in content

    def test_renders_with_verify(self) -> None:
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/assess_me.md",
            conversation="user: hello",
            verify="Claim A is true\nClaim B is false",
        )
        assert "Items to Verify" in content
        assert "Claim A is true" in content
        assert "Claim B is false" in content

    def test_renders_from_assess_me_function(self) -> None:
        """Integration: assess_me() renders template with Thinking patterns."""
        from nanobot.agent.assess_me import format_conversation

        conv = format_conversation([
            {"role": "user", "content": "check this"},
        ])
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/assess_me.md",
            conversation=conv,
            verify="",
        )
        # The formatted conversation should appear in the prompt
        assert "check this" in content
        assert "Thinking patterns" in content
