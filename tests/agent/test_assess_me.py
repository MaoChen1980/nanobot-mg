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
    """Test _maybe_assess triggers correctly with llm_request_count.

    The interval trigger should fire based on the persistent LLM request counter
    (session.metadata["llm_request_count"]), not message list scanning — so
    dense tool-call sequences get periodic direction checks.
    """

    def _make_handler(self):
        from nanobot.agent.loop_message_handlers import UserMessageHandler

        loop = MagicMock()
        loop._db = None  # no DB → default interval
        return UserMessageHandler(loop)

    @pytest.mark.asyncio
    async def test_no_trigger_when_count_below_interval(self) -> None:
        """llm_request_count < interval (10) → no trigger."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 4
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_on_first_turn(self) -> None:
        """llm_request_count == 0 → no trigger (clean session, skip guard)."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_at_interval(self) -> None:
        """llm_request_count % 10 == 0 → trigger assess_me."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
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
        """llm_request_count == 20 also triggers (next multiple)."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 20
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
        """llm_request_count == 11 (just past 10, 11%10=1) → no trigger."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 11
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_compress_triggers_regardless_of_count(self) -> None:
        """compress_triggered=True fires even with 0 llm_request_count."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
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
        session.metadata["llm_request_count"] = 10
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
        session.metadata["llm_request_count"] = 10
        history = session.format_history()
        original_len = len(history)

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.side_effect = RuntimeError("LLM down")
            await handler._maybe_assess(session, history)

            assert len(history) == original_len, "history unchanged after exception"

    @pytest.mark.asyncio
    async def test_llm_count_matters_not_user_count(self) -> None:
        """High user message count but low llm_request_count → no trigger.

        The mechanism is: llm_request_count tracks persistent LLM API calls,
        not message types. So dense user messages without corresponding LLM
        calls should not trigger assess_me.
        """
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        # 15 user messages but only 5 llm_request_count
        session.metadata["llm_request_count"] = 5
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

    @pytest.mark.asyncio
    async def test_maybe_assess_db_assess_interval(self) -> None:
        """DB-stored assess_interval overrides the default 10."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = MagicMock()
        loop._db = MagicMock()
        loop._db.get_metadata.return_value = "5"
        handler = UserMessageHandler(loop)

        session = Session(key="test")
        # llm_request_count=5 should trigger with interval=5, but not with default=10
        session.metadata["llm_request_count"] = 5
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = "analysis at 5"
            await handler._maybe_assess(session, history)

            mock_assess.assert_called_once()
            loop._db.get_metadata.assert_called_with("assess_interval")

    @pytest.mark.asyncio
    async def test_maybe_assess_db_interval_skips_when_no_db(self) -> None:
        """No DB (loop._db is None) → falls back to default 10, llm_count=5 doesn't trigger."""
        from nanobot.session.manager import Session

        handler = self._make_handler()  # _db = None
        session = Session(key="test")
        session.metadata["llm_request_count"] = 5
        history = session.format_history()

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            await handler._maybe_assess(session, history)
            mock_assess.assert_not_called()


# =========================================================================
# _finalize_turn — llm_request_count persistence
# =========================================================================


class TestFinalizeTurn:
    """Test _finalize_turn correctly persists llm_request_count in session metadata."""

    def _make_loop(self):
        from nanobot.agent.loop import AgentLoop

        loop = MagicMock(spec=AgentLoop)
        loop.lifecycle = MagicMock()
        loop._pt_save_interval = 999  # never trigger .pt save in tests
        return loop

    @pytest.mark.asyncio
    async def test_accumulates_llm_request_count(self) -> None:
        """total_llm_requests=5 → session.metadata['llm_request_count'] == 5."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        await handler._finalize_turn(session, all_msgs, 0, False, "hi", total_llm_requests=5)

        assert session.metadata.get("llm_request_count") == 5

    @pytest.mark.asyncio
    async def test_accumulates_multiple_times(self) -> None:
        """Multiple finalize calls accumulate llm_request_count."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        await handler._finalize_turn(session, all_msgs, 0, False, "hi", total_llm_requests=3)
        await handler._finalize_turn(session, all_msgs, 0, False, "hi again", total_llm_requests=2)

        assert session.metadata.get("llm_request_count") == 5

    @pytest.mark.asyncio
    async def test_zero_total_does_not_change_count(self) -> None:
        """total_llm_requests=0 → count unchanged."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
        all_msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        await handler._finalize_turn(session, all_msgs, 0, False, "hi", total_llm_requests=0)

        assert session.metadata.get("llm_request_count") == 10

    @pytest.mark.asyncio
    async def test_assistant_turn_count_still_tracked(self) -> None:
        """_finalize_turn also tracks assistant_turn_count alongside llm_request_count."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "follow-up"},
            {"role": "assistant", "content": "second"},
        ]

        await handler._finalize_turn(session, all_msgs, 1, False, "second", total_llm_requests=3)

        assert session.metadata.get("llm_request_count") == 3
        assert session.metadata.get("assistant_turn_count") == 2


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
    """Verify the assess_me.md template includes all required sections."""

    def _render(self, **kwargs: Any) -> str:
        from nanobot.utils.prompt_templates import render_template
        return render_template("agent/assess_me.md", **kwargs)

    def _section_names(self) -> list[str]:
        return [
            "有用信息盘点",
            "信息缺口",
            "假设检查",
            "进度与状态",
            "未来方向",
            "思维模式",
            "可复用模式",
        ]

    def test_includes_all_sections(self) -> None:
        content = self._render(
            conversation="user: hello\nassistant: hi",
            verify="",
        )
        for section in self._section_names():
            assert section in content, (
                f"Template should include section: {section}"
            )

    def test_renders_without_verify(self) -> None:
        content = self._render(conversation="user: hello")
        assert "有用信息盘点" in content
        assert "Items to Verify" not in content

    def test_renders_with_verify(self) -> None:
        content = self._render(
            conversation="user: hello",
            verify="Claim A is true\nClaim B is false",
        )
        assert "Items to Verify" in content
        assert "Claim A is true" in content
        assert "Claim B is false" in content

    def test_renders_from_assess_me_function(self) -> None:
        """Integration: assess_me() renders template with conversation."""
        from nanobot.agent.assess_me import format_conversation

        conv = format_conversation([
            {"role": "user", "content": "check this"},
        ])
        content = self._render(conversation=conv, verify="")
        assert "check this" in content
        assert "有用信息盘点" in content


# =========================================================================
# Skill creation trigger — "值得创建 skill" detection + dedup
# =========================================================================


class TestSkillCreationTrigger:
    """Test '值得创建 skill' triggers background skill-creation sub-runner.

    Regression tests covering:
    - Detection of the trigger marker in assess_me output
    - Inflight dedup (same result -> no duplicate spawn)
    - Distinct results spawn independently
    - No marker -> no spawn
    """

    def _make_handler(self):
        from nanobot.agent.loop_message_handlers import UserMessageHandler

        loop = MagicMock()
        loop._db = None
        return UserMessageHandler(loop)

    def _make_msgs(self, n_assistants: int) -> list[dict]:
        """Build alternating user/assistant messages with *n_assistants* assistant turns."""
        msgs: list[dict] = []
        for i in range(n_assistants):
            msgs.append({"role": "user", "content": f"msg {i}"})
            msgs.append({"role": "assistant", "content": f"resp {i}"})
        return msgs

    @pytest.mark.asyncio
    async def test_trigger_spawns_skill_creation(self) -> None:
        """'值得创建 skill' in assess_me result -> spawns skill creation."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
        session.messages = self._make_msgs(10)
        history = session.format_history()

        with (
            patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess,
            patch("nanobot.agent.tools.debug_root_cause.DebugRootCauseTool") as mock_dcr,
            patch.object(handler, "_spawn_skill_creator", new_callable=AsyncMock) as mock_spawn,
        ):
            mock_assess.return_value = "值得创建 skill: verify-api-response"
            dcr_instance = MagicMock()
            dcr_instance.execute = AsyncMock(return_value="root cause analysis")
            mock_dcr.return_value = dcr_instance

            await handler._maybe_assess(session, history)

            mock_assess.assert_called_once()
            mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_same_result_skips_duplicate(self) -> None:
        """Same '值得创建 skill' result twice -> only one spawn (inflight dedup)."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
        session.messages = self._make_msgs(10)
        history = session.format_history()

        with (
            patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess,
            patch("nanobot.agent.tools.debug_root_cause.DebugRootCauseTool") as mock_dcr,
            patch.object(handler, "_spawn_skill_creator", new_callable=AsyncMock) as mock_spawn,
        ):
            mock_assess.return_value = "值得创建 skill: verify-api-response"
            dcr_instance = MagicMock()
            dcr_instance.execute = AsyncMock(return_value="analysis")
            mock_dcr.return_value = dcr_instance

            # First call -- should spawn
            await handler._maybe_assess(session, history)
            assert mock_spawn.call_count == 1

            # Second call with same handler + same result -> dedup
            await handler._maybe_assess(session, history)
            assert mock_spawn.call_count == 1  # not incremented

    @pytest.mark.asyncio
    async def test_different_result_allows_new_spawn(self) -> None:
        """Different '值得创建 skill' results -> each spawns independently."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
        session.messages = self._make_msgs(10)
        history = session.format_history()

        with (
            patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess,
            patch("nanobot.agent.tools.debug_root_cause.DebugRootCauseTool") as mock_dcr,
            patch.object(handler, "_spawn_skill_creator", new_callable=AsyncMock) as mock_spawn,
        ):
            dcr_instance = MagicMock()
            dcr_instance.execute = AsyncMock(return_value="analysis")
            mock_dcr.return_value = dcr_instance

            # First pattern
            mock_assess.return_value = "值得创建 skill: verify-api-response"
            await handler._maybe_assess(session, history)
            assert mock_spawn.call_count == 1

            # Second pattern (different) -- should spawn separately
            mock_assess.return_value = "值得创建 skill: check-tool-before-assuming"
            await handler._maybe_assess(session, history)
            assert mock_spawn.call_count == 2

    @pytest.mark.asyncio
    async def test_no_marker_does_not_spawn(self) -> None:
        """Normal assess_me output without '值得创建 skill' -> no spawn."""
        from nanobot.session.manager import Session

        handler = self._make_handler()
        session = Session(key="test")
        session.metadata["llm_request_count"] = 10
        session.messages = self._make_msgs(10)
        history = session.format_history()

        with (
            patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess,
            patch("nanobot.agent.tools.debug_root_cause.DebugRootCauseTool") as mock_dcr,
            patch.object(handler, "_spawn_skill_creator", new_callable=AsyncMock) as mock_spawn,
        ):
            mock_assess.return_value = "agent is on track, no reusable patterns found"
            dcr_instance = MagicMock()
            dcr_instance.execute = AsyncMock(return_value="analysis")
            mock_dcr.return_value = dcr_instance

            await handler._maybe_assess(session, history)

            mock_spawn.assert_not_called()


# =========================================================================
# Skill creation template -- integration check
# =========================================================================


class TestSkillCreationTemplate:
    """Verify the skill_creation.md template renders correctly."""

    def test_renders_with_assess_result(self) -> None:
        """Template renders with assess_me output and workspace path."""
        from nanobot.utils.prompt_templates import render_template

        assess_result = "值得创建 skill: verify-api-response"
        content = render_template(
            "agent/_instructions/skill_creation.md",
            assess_result=assess_result,
            workspace_path="/tmp/workspace",
        )
        assert assess_result in content
        assert "## Action" in content
        assert "## Verification" in content
        assert "skills/" in content

    def test_renders_with_empty_result(self) -> None:
        """Template renders with empty assess_me result (edge case)."""
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/_instructions/skill_creation.md",
            assess_result="",
            workspace_path="/tmp/workspace",
        )
        assert "## Action" in content
        assert "## Verification" in content
