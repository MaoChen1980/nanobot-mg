"""Tests for MessagePipe — LLM call pipe with overflow retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.agent.message_pipe import (
    MessagePipe,
    _has_context_window_error,
    _is_overflow,
)
from nanobot.providers.base import LLMResponse
from nanobot.session.manager import Session


def _make_overflow_response(content: str = "context window exceeded") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="error", error_kind="context_length")


def _make_success_response(content: str = "hello") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


class TestOverflowDetection:
    def test_detects_context_window_markers(self):
        assert _has_context_window_error("context window exceeded") is True
        assert _has_context_window_error("maximum context length reached") is True
        assert _has_context_window_error("prompt is too long") is True
        assert _has_context_window_error("too many tokens") is True
        assert _has_context_window_error("token limit") is True
        assert _has_context_window_error("context length") is True

    def test_returns_false_for_other_errors(self):
        assert _has_context_window_error("rate limit exceeded") is False
        assert _has_context_window_error("server error") is False
        assert _has_context_window_error(None) is False
        assert _has_context_window_error("") is False

    def test_is_overflow_checks_finish_reason_and_content(self):
        resp = _make_overflow_response()
        assert _is_overflow(resp) is True

    def test_is_overflow_requires_error_finish_reason(self):
        resp = _make_success_response()
        assert _is_overflow(resp) is False

    def test_is_overflow_false_on_non_error_finish_reason(self):
        resp = LLMResponse(content="context window", finish_reason="length")
        assert _is_overflow(resp) is False


class TestMessagePipeComplete:
    """Non-streaming path."""

    async def test_returns_response_on_success(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_success_response("ok"))
        llm_set_llm(provider, "test-model")

        result = await pipe.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )

        assert result.content == "ok"
        provider.chat_with_retry.assert_awaited_once()

    async def test_retries_on_overflow(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("retried ok"),
        ])
        llm_set_llm(provider, "test-model")

        result = await pipe.complete(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous response"},
                {"role": "user", "content": "followup"},
            ],
            model="test-model",
        )

        assert result.content == "retried ok"
        assert provider.chat_with_retry.await_count == 2

    async def test_exhausts_retries(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_overflow_response())
        llm_set_llm(provider, "test-model")

        result = await pipe.complete(
            messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            model="test-model",
        )

        assert _is_overflow(result)
        # MAX_RETRIES(3) loop iterations + 1 final fallback = 5 total
        assert provider.chat_with_retry.await_count == 5


class TestMessagePipeCompleteStream:
    """Streaming path."""

    async def test_returns_response_on_success(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("stream ok"))
        llm_set_llm(provider, "test-model")
        on_delta = AsyncMock()
        on_reasoning = AsyncMock()

        result = await pipe.complete_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            on_content_delta=on_delta,
            on_reasoning_delta=on_reasoning,
        )

        assert result.content == "stream ok"
        provider.chat_stream_with_retry.assert_awaited_once()

    async def test_retries_on_overflow(self):
        pipe = MessagePipe()
        provider = MagicMock()
        call_count = 0

        async def _stream_with_retry(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_overflow_response()
            return _make_success_response("retried stream")

        provider.chat_stream_with_retry = AsyncMock(side_effect=_stream_with_retry)
        llm_set_llm(provider, "test-model")

        result = await pipe.complete_stream(
            messages=[
                {"role": "system", "content": "x"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
            model="test-model",
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )

        assert result.content == "retried stream"
        # Overflow → _compress(sumarize) → retry = 3 calls
        assert call_count == 3


class TestCompress:
    """_compress compresses oldest turns into a summary."""

    async def test_returns_unchanged_when_few_messages(self):
        pipe = MessagePipe()

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

        result = await pipe._compress(messages)
        assert result == messages  # no compression needed

    async def test_appends_latest_user_if_missing_after_compress(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn1 user"},
            {"role": "assistant", "content": "turn1 asst"},
            {"role": "user", "content": "latest q"},
        ]

        result = await pipe._compress(messages)
        # Latest user message should still be at the end
        assert result[-1]["content"] == "latest q"

    async def test_handles_summary_failure_gracefully(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(side_effect=RuntimeError("LLM down"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]

        with patch("asyncio.sleep", AsyncMock()):
            result = await pipe._compress(messages)
        # Should still produce a result without summary
        assert result[0]["role"] == "system"
        assert len(result) > 0


class TestSplitTurnsByAssistant:
    """Session._split_turns_by_assistant used by MessagePipe._compress."""

    def test_splits_correctly(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        turns = Session._split_turns_by_assistant(msgs)
        # Turns: [system, user] (leading non-assistant), [assistant a1, user q2], [assistant a2]
        assert len(turns) == 3
        assert turns[0][0]["content"] == "sys"
        assert turns[1][0]["content"] == "a1"
        assert turns[2][0]["content"] == "a2"

    def test_handles_leading_user_message(self):
        msgs = [
            {"role": "user", "content": "q1"},
        ]
        turns = Session._split_turns_by_assistant(msgs)
        assert len(turns) == 1
        assert turns[0][0]["role"] == "user"
