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

    def test_tailing_continue_directive(self) -> None:
        """Verify the injected message includes a continue-working directive.

        The assessment is context, not a user query — the trailing directive
        tells the LLM to use it as reference and continue the original task.
        """
        msg = build_assessment_message("some analysis")
        content = msg["content"]
        # Content still contains the assessment body
        assert "some analysis" in content
        # Starts with [assess] tag (detection relies on this)
        assert content.startswith("[assess]")
        # Ends with the continue directive
        assert "继续推进原始任务" in content
        assert "无需回应此消息" in content




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

    # ------------------------------------------------------------------
    # Rebuild tests — session.messages rebuilt from all_msgs to persist
    # compressed history. These use a real _append_turn_to_session.
    # ------------------------------------------------------------------

    def _make_real_loop(self):
        """Build a mock loop with a real _append_turn_to_session method."""
        from nanobot.agent.loop import AgentLoop

        loop = MagicMock(spec=AgentLoop)
        loop.lifecycle = MagicMock()
        loop._pt_save_interval = 999
        loop.max_tool_result_chars = 1000
        loop._sanitize_persisted_blocks = MagicMock(return_value=[])

        # Bind the real _append_turn_to_session
        loop._append_turn_to_session = (
            AgentLoop._append_turn_to_session.__get__(loop, AgentLoop)
        )
        return loop

    @pytest.mark.asyncio
    async def test_rebuild_basic(self) -> None:
        """Rebuild: history + current turn end up in session.messages."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello", "timestamp": "t1"},
            {"role": "assistant", "content": "hi", "timestamp": "t2"},
        ]

        await handler._finalize_turn(session, all_msgs, 2, False, "hi")

        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "hello"
        assert session.messages[1]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_rebuild_with_instructions(self) -> None:
        """Rebuild: instructions at index 1 are not persisted."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "## Instructions\n\nDo X"},
            {"role": "user", "content": "hello", "timestamp": "t1"},
            {"role": "assistant", "content": "hi", "timestamp": "t2"},
        ]

        await handler._finalize_turn(session, all_msgs, 3, False, "hi")

        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "hello"
        assert session.messages[1]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_rebuild_persisted_early_preserves_user_msg(self) -> None:
        """Rebuild: early-persisted user message keeps _message_id and timestamp."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        session.messages = [
            {"role": "user", "content": "hello", "timestamp": "t1", "_message_id": "msg_1"}
        ]
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello", "timestamp": "t1"},
            {"role": "assistant", "content": "hi", "timestamp": "t2"},
        ]

        await handler._finalize_turn(session, all_msgs, 2, True, "hi")

        assert len(session.messages) == 2
        assert session.messages[0].get("_message_id") == "msg_1"
        assert session.messages[0].get("timestamp") == "t1"
        assert session.messages[1]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_rebuild_with_compressed_history(self) -> None:
        """Rebuild: compressed synthetic pair + current turn survive finalize."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "[compressed summary of earlier conversation]"},
            {"role": "assistant", "content": "[summary response]"},
            {"role": "user", "content": "current query", "timestamp": "t3"},
            {"role": "assistant", "content": "current response", "timestamp": "t4"},
        ]

        await handler._finalize_turn(session, all_msgs, 3, False, "current response")

        # History: [compressed summary], Current: [summary response, current query, current response]
        assert len(session.messages) == 4
        assert session.messages[0]["content"] == "[compressed summary of earlier conversation]"
        assert session.messages[1]["content"] == "[summary response]"
        assert session.messages[2]["content"] == "current query"
        assert session.messages[3]["content"] == "current response"

    @pytest.mark.asyncio
    async def test_rebuild_summary_injected_key_set_when_summary_exists(self) -> None:
        """Rebuild: _summary_injected_key set when session._last_summary exists."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        session._last_summary = "previous summary"
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello", "timestamp": "t1"},
            {"role": "assistant", "content": "hi", "timestamp": "t2"},
        ]

        await handler._finalize_turn(session, all_msgs, 2, False, "hi")

        assert session.metadata.get("_summary_injected_key") == "previous summary"

    @pytest.mark.asyncio
    async def test_rebuild_empty_assistant_filtered(self) -> None:
        """Rebuild: empty assistant messages are filtered by _append_turn_to_session."""
        from nanobot.agent.loop_message_handlers import UserMessageHandler
        from nanobot.session.manager import Session

        loop = self._make_real_loop()
        handler = UserMessageHandler(loop)
        session = Session(key="test")
        all_msgs = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello", "timestamp": "t1"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "real response", "timestamp": "t2"},
        ]

        await handler._finalize_turn(session, all_msgs, 2, False, "real response")

        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "hello"
        assert session.messages[1]["content"] == "real response"


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
        """Returns content from LLM call as-is."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = '{"status": "ok", "summary": "all good", "content": ""}'

            result = await assess_me(
                [{"role": "user", "content": "hello"}],
            )
            assert result is not None
            assert '"status": "ok"' in result
            mock_fn.assert_called_once()  # JSON detected, no retry

    @pytest.mark.asyncio
    async def test_retries_on_non_json(self) -> None:
        """Non-JSON response triggers a retry with stricter instruction."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            # First call returns chat text, second returns JSON
            mock_fn.side_effect = [
                AsyncMock(content="Hi, how can I help?", finish_reason="stop"),
                AsyncMock(content='{"status": "ok", "summary": "retried"}', finish_reason="stop"),
            ]

            result = await assess_me(
                [{"role": "user", "content": "hello"}],
            )
            assert result is not None
            assert '"status": "ok"' in result
            assert mock_fn.call_count == 2  # first failed, retried once

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
            mock_fn.return_value.content = '{"status": "ok", "summary": "analysis"}'

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

    @pytest.mark.asyncio
    async def test_disables_reasoning_for_assess_me(self) -> None:
        """assess_me passes reasoning_effort='none' to disable chain-of-thought."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = '{"status": "ok"}'

            await assess_me(
                [{"role": "user", "content": "hello"}],
            )

            _, kwargs = mock_fn.call_args
            assert kwargs.get("reasoning_effort") == "none"


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
            mock_assess.return_value = '{"status": "findings", "summary": "test finding", "content": "some detail"}'
            result = await callback(msgs)

            assert result  # AssessResult with injected=True is truthy
            assert not result.needs_revision
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

            assert not result  # AssessResult() is falsy
            assert not result.needs_revision
            assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_callback_sets_needs_revision(self) -> None:
        from nanobot.session.manager import Session

        loop = self._make_loop()
        session = Session(key="test")
        callback = loop._make_retry_assess_callback(session)
        assert callback is not None

        msgs = [{"role": "user", "content": "hello"}]

        with patch("nanobot.agent.assess_me.assess_me", new_callable=AsyncMock) as mock_assess:
            mock_assess.return_value = (
                '{"status": "findings", "summary": "bad output", '
                '"content": "needs fix", "needs_revision": true}'
            )
            result = await callback(msgs)

            assert result  # injected
            assert result.needs_revision
            assert len(msgs) == 2
            # The injection text should contain the fix instruction
            assert "请直接修正内容" in msgs[1]["content"]


class TestExtractAssessJson:
    """Direct tests for AgentLoop._extract_assess_json."""

    @pytest.mark.parametrize("input_text,expected", [
        # Clean JSON
        ('{"status": "ok"}', {"status": "ok"}),
        # Trailing text after JSON
        ('{"status": "findings"}\n\nSome extra commentary', {"status": "findings"}),
        # <think> tags
        ('<think>Let me analyze...</think>{"status": "ok"}', {"status": "ok"}),
        # Multi-line <think>
        ('<think>\nline1\nline2\n</think>\n{"status": "findings"}', {"status": "findings"}),
        # Code fences with json tag
        ('```json\n{"status": "ok"}\n```', {"status": "ok"}),
        # Code fences without tag
        ('```\n{"status": "ok"}\n```', {"status": "ok"}),
        # Code fence + trailing text after closing
        ('```json\n{"s": "ok"}\n```\nsome text', {"s": "ok"}),
        # Leading markdown heading
        ('## 评估结果\n\n{"status": "findings", "summary": "test"}', {"status": "findings", "summary": "test"}),
        # Nested braces in JSON string value
        ('{"msg": "hello { world }", "n": 1}', {"msg": "hello { world }", "n": 1}),
        # Escaped quotes
        ('{"msg": "hello \\"world\\""}', {"msg": 'hello "world"'}),
        # Unicode escapes
        ('{"c": "\\u4e16\\u754c"}', {"c": "世界"}),
        # No JSON at all
        ("Hello world", None),
        # Empty string
        ("", None),
        # Only <think> with no JSON
        ("<think>processing</think>", None),
        # Only code fence with no JSON
        ("```\njust text\n```", None),
    ])
    def test_extract_json(self, input_text: str, expected) -> None:
        from nanobot.agent.loop import AgentLoop

        result = AgentLoop._extract_assess_json(input_text)
        assert result == expected, f"Input: {input_text!r}\nExpected: {expected!r}\nGot: {result!r}"


class TestAssessMePipeline:
    """Integration-style: assess_me() → _extract_assess_json() with realistic response."""

    @pytest.mark.asyncio
    async def test_pipeline_ok_json(self) -> None:
        """Full pipeline: assess_me returns valid JSON → extract parses it."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = (
                '{"status": "ok", "summary": "一切正常", "content": "",'
                '"need_drc": false, "blocker": null, "skill_pattern": null,'
                '"needs_revision": false}'
            )

            from nanobot.agent.loop import AgentLoop
            raw = await assess_me([{"role": "user", "content": "hello"}])
            parsed = AgentLoop._extract_assess_json(raw)

            assert parsed is not None, f"assess_me returned non-JSON: {raw[:100]}"
            assert parsed["status"] == "ok"
            assert parsed["need_drc"] is False
            assert parsed.get("skill_pattern") is None

    @pytest.mark.asyncio
    async def test_pipeline_findings_json(self) -> None:
        """Full pipeline: findings JSON with content is parsed correctly."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = (
                '{"status": "findings", "summary": "agent 偏离任务",'
                '"content": "agent 回复了无关内容，用户请求是分析数据",'
                '"need_drc": false, "blocker": null, "skill_pattern": "always-check-tool-output",'
                '"needs_revision": true}'
            )

            from nanobot.agent.loop import AgentLoop
            raw = await assess_me([{"role": "user", "content": "分析数据"}])
            parsed = AgentLoop._extract_assess_json(raw)

            assert parsed is not None
            assert parsed["status"] == "findings"
            assert parsed["needs_revision"] is True
            assert parsed["skill_pattern"] == "always-check-tool-output"

    @pytest.mark.asyncio
    async def test_pipeline_code_fence_response(self) -> None:
        """assess_me wrapped in ```json code fence — still parses."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = (
                '```json\n{"status": "ok", "summary": "all good", "content": "",'
                '"need_drc": false, "blocker": null, "skill_pattern": null,'
                '"needs_revision": false}\n```'
            )

            from nanobot.agent.loop import AgentLoop
            raw = await assess_me([{"role": "user", "content": "hello"}])
            parsed = AgentLoop._extract_assess_json(raw)

            assert parsed is not None
            assert parsed["status"] == "ok"

    @pytest.mark.asyncio
    async def test_pipeline_non_json_response(self) -> None:
        """assess_me returns plain text → extract returns None (not crash)."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = "Hi, how can I help you today?"

            from nanobot.agent.loop import AgentLoop
            raw = await assess_me([{"role": "user", "content": "hi"}])
            parsed = AgentLoop._extract_assess_json(raw)

            assert parsed is None  # non-JSON gracefully handled

    @pytest.mark.asyncio
    async def test_pipeline_empty_response(self) -> None:
        """assess_me returns empty string → extract returns None."""
        with patch("nanobot.agent.assess_me.chat_stream_with_retry", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value.content = ""

            raw = await assess_me([{"role": "user", "content": "hi"}])
            assert raw == ""  # assess_me returns empty on empty content


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
            has_active_task=True,
        )
        for section in self._section_names():
            assert section in content, (
                f"Template should include section: {section}"
            )

    def test_omits_task_sections_when_no_active_task(self) -> None:
        content = self._render(
            conversation="user: hello\nassistant: hi",
            verify="",
            has_active_task=False,
        )
        # Task-specific section headers should be absent
        for section_header in ("#### 3. 信息缺口", "#### 4. 假设检查", "#### 5. 进度与状态", "#### 6. 未来方向"):
            assert section_header not in content, (
                f"Template should NOT include task section header when has_active_task=False: {section_header}"
            )
        # General sections should still be present
        assert "事实合规" in content
        assert "逻辑一致" in content
        assert "行为检视" in content
        assert "思维模式" in content
        assert "可复用模式" in content

    def test_renders_without_verify(self) -> None:
        content = self._render(conversation="user: hello")
        assert "事实合规" in content
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
        assert "事实合规" in content


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
        assert "## Steps" in content
        assert "## When to Use" in content
        assert "## Verification" in content
        assert "## Pitfalls" in content
        assert "**Self-optimization**" in content
        assert "skills/" in content

    def test_renders_with_empty_result(self) -> None:
        """Template renders with empty assess_me result (edge case)."""
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/_instructions/skill_creation.md",
            assess_result="",
            workspace_path="/tmp/workspace",
        )
        assert "## Steps" in content
        assert "## When to Use" in content
        assert "## Verification" in content
        assert "## Pitfalls" in content
        assert "**Self-optimization**" in content


# ---------------------------------------------------------------------------
# Extractor skill creation template -- integration check
# ---------------------------------------------------------------------------


class TestExtractorSkillCreatorTemplate:
    """Verify the extractor_skill_creator.md template renders correctly."""

    def test_renders_with_workspace_path(self) -> None:
        """Template renders with workspace_path variable."""
        from nanobot.utils.prompt_templates import render_template

        content = render_template(
            "agent/extractor_skill_creator.md",
            workspace_path="/tmp/workspace",
        )
        assert "## 流程" in content
        assert "glob_tool" in content
        assert "exec_tool" in content
        assert "grep_tool" in content
        assert "read_file" in content
        assert "write_file" in content
        assert "edit_file" in content
        assert "## Steps" in content or "步骤" in content
        assert "## 决策指引" in content
        assert "## Skill 格式" in content
        assert "## Pitfalls" in content or "Pitfalls" in content
        assert "**Self-optimization**" in content
        assert "/tmp/workspace" in content
