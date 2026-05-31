"""Tests for MessagePipe — LLM call pipe with overflow retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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

        result = await pipe.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            provider=provider,
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

        result = await pipe.complete(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous response"},
                {"role": "user", "content": "followup"},
            ],
            model="test-model",
            provider=provider,
        )

        assert result.content == "retried ok"
        assert provider.chat_with_retry.await_count == 2

    async def test_exhausts_retries(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_overflow_response())

        result = await pipe.complete(
            messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            model="test-model",
            provider=provider,
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
        on_delta = AsyncMock()
        on_reasoning = AsyncMock()

        result = await pipe.complete_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            provider=provider,
            on_content_delta=on_delta,
            on_reasoning_delta=on_reasoning,
        )

        assert result.content == "stream ok"
        provider.chat_stream_with_retry.assert_awaited_once()

    async def test_retries_on_overflow(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("retried stream"),
        ])

        result = await pipe.complete_stream(
            messages=[
                {"role": "system", "content": "x"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
            model="test-model",
            provider=provider,
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )

        assert result.content == "retried stream"
        assert provider.chat_stream_with_retry.await_count == 2


class TestCompress:
    """_compress compresses oldest turns into a summary."""

    async def test_compresses_old_turns(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_success_response("summary of old turns"))

        messages = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "turn1 user"},
            {"role": "assistant", "content": "turn1 asst"},
            {"role": "user", "content": "turn2 user"},
            {"role": "assistant", "content": "turn2 asst"},
            {"role": "user", "content": "current question"},
        ]

        result = await pipe._compress(messages, provider, "test-model")

        # System prompt preserved
        assert result[0]["role"] == "system"
        # Summary pair injected
        assert any(m.get("status") == "synthetic" for m in result)
        # Current user question preserved
        assert any(m["content"] == "current question" for m in result)

    async def test_returns_unchanged_when_few_messages(self):
        pipe = MessagePipe()
        provider = MagicMock()

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

        result = await pipe._compress(messages, provider, "test-model")
        assert result == messages  # no compression needed

    async def test_appends_latest_user_if_missing_after_compress(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_success_response("summary"))

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn1 user"},
            {"role": "assistant", "content": "turn1 asst"},
            {"role": "user", "content": "latest q"},
        ]

        result = await pipe._compress(messages, provider, "test-model")
        # Latest user message should still be at the end
        assert result[-1]["content"] == "latest q"

    async def test_handles_summary_failure_gracefully(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]

        with patch("asyncio.sleep", AsyncMock()):
            result = await pipe._compress(messages, provider, "test-model")
        # Should still produce a result without summary
        assert result[0]["role"] == "system"
        assert len(result) > 0


class TestSummarizeTurns:
    """_summarize_turns constructs prompt and calls LLM."""

    async def test_returns_summary(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_success_response("key fact: 42"))

        turns = [
            {"role": "user", "content": "What is the answer?"},
            {"role": "assistant", "content": "The answer is 42."},
        ]

        def strip_think(x):
            return x

        summary = await pipe._summarize_turns(turns, None, provider, "test-model", strip_think)
        assert summary == "key fact: 42"
        provider.chat.assert_awaited_once()
        prompt = provider.chat.call_args[0][0][0]["content"]
        assert "The answer is 42." in prompt

    async def test_includes_future_context_in_prompt(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_success_response("summary"))

        turns = [{"role": "user", "content": "old msg"}]
        future = [{"role": "assistant", "content": "future context"}]

        def strip_think(x):
            return x

        await pipe._summarize_turns(turns, future, provider, "test-model", strip_think)
        prompt = provider.chat.call_args[0][0][0]["content"]
        assert "future context" in prompt
        assert "old msg" in prompt

    async def test_returns_empty_on_provider_error(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        def strip_think(x):
            return x

        with patch("asyncio.sleep", AsyncMock()):
            summary = await pipe._summarize_turns([], None, provider, "test-model", strip_think)
        assert summary == ""
        assert provider.chat.await_count == 6

    async def test_prompt_contains_guidelines(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_success_response(""))

        def strip_think(x):
            return x

        await pipe._summarize_turns([], None, provider, "test-model", strip_think)
        prompt = provider.chat.call_args[0][0][0]["content"]
        assert "方向（由你判断" in prompt
        assert "最重要" in prompt
        assert "参考" in prompt

    async def test_retries_on_network_error_then_succeeds(self):
        pipe = MessagePipe()
        provider = MagicMock()
        call_count = 0

        async def _chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 6:
                raise RuntimeError("connection reset")
            return _make_success_response("final summary")

        provider.chat = AsyncMock(side_effect=_chat)

        def strip_think(x):
            return x

        with patch("asyncio.sleep", AsyncMock()):
            summary = await pipe._summarize_turns(
                [{"role": "user", "content": "msg"}], None,
                provider, "test-model", strip_think,
            )
        assert summary == "final summary"
        assert provider.chat.await_count == 6

    async def test_retries_on_overflow_with_half_content(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("reduced summary"),
        ])

        turns = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]

        def strip_think(x):
            return x

        with patch("asyncio.sleep", AsyncMock()):
            summary = await pipe._summarize_turns(turns, None, provider, "test-model", strip_think)
        assert summary == "reduced summary"
        assert provider.chat.await_count == 2
        # Second call should have fewer turns (reduced by half)
        second_prompt = provider.chat.call_args[0][0][0]["content"]
        assert "<user>\nc\n</user>" in second_prompt  # kept half
        assert "<user>\na\n</user>" not in second_prompt  # oldest half dropped

    async def test_returns_empty_when_all_overflow_retries_exhausted(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_overflow_response())

        # 8 turns: halved to 4 → 2 → 1 → can't reduce further
        turns = [{"role": "user", "content": f"msg {i}"} for i in range(8)]

        def strip_think(x):
            return x

        with patch("asyncio.sleep", AsyncMock()):
            summary = await pipe._summarize_turns(turns, None, provider, "test-model", strip_think)
        assert summary == ""
        assert provider.chat.await_count == 4


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
