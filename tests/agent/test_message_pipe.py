"""Tests for MessagePipe — LLM call pipe with overflow retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.agent.message_pipe import (
    MessagePipe,
    _has_context_window_error,
    _is_overflow,
)
from nanobot.agent.compressor import CompressEvent, Compressor
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

        result, compressed = await pipe.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )

        assert result.content == "ok"
        assert compressed is None
        provider.chat_with_retry.assert_awaited_once()

    async def test_no_preflight_compression_with_large_messages(self):
        """Pre-flight compression removed; large messages pass through
        without triggering _compress (only 400 overflow triggers it)."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_success_response("ok"))
        llm_set_llm(provider, "test-model")

        messages = [{"role": "system", "content": "sys"}]
        for i in range(50):
            messages.append({"role": "user", "content": f"q{i}"})
            messages.append({"role": "assistant", "content": f"a{i}"})

        result, compressed = await pipe.complete(
            messages=messages,
            budget=10_000,  # tiny budget, many messages — no pre-flight
            model="test-model",
        )

        assert result.content == "ok"
        assert compressed is None
        provider.chat_with_retry.assert_awaited_once()

    async def test_retries_on_overflow(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("retried ok"),
        ])
        # _compress calls chat_stream_with_retry for summarization
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        result, compressed = await pipe.complete(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous response"},
                {"role": "user", "content": "followup"},
            ],
            model="test-model",
        )

        assert result.content == "retried ok"
        assert compressed is not None
        assert provider.chat_with_retry.await_count == 2

    async def test_exhausts_retries(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_overflow_response())
        llm_set_llm(provider, "test-model")

        result, compressed = await pipe.complete(
            messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            model="test-model",
        )

        assert _is_overflow(result)
        assert compressed is not None
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

        result, compressed = await pipe.complete_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            on_content_delta=on_delta,
            on_reasoning_delta=on_reasoning,
        )

        assert result.content == "stream ok"
        assert compressed is None
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
            # call 2: compression (summarize_turns), call 3: main retry
            return _make_success_response("retried stream")

        provider.chat_stream_with_retry = AsyncMock(side_effect=_stream_with_retry)
        llm_set_llm(provider, "test-model")

        result, compressed = await pipe.complete_stream(
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
        assert compressed is not None
        # Overflow → _compress(summarize) → retry = 3 calls
        assert call_count == 3


class TestCompress:
    """_compress compresses oldest turns into a summary."""

    async def test_returns_unchanged_when_few_messages(self):
        pipe = MessagePipe()

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

        result, event = await pipe._compress(messages)
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

        result, event = await pipe._compress(messages)
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
            result, event = await pipe._compress(messages)
        # Should still produce a result without summary
        assert result[0]["role"] == "system"
        assert len(result) > 0


class TestCompressWithBudget:
    """_compress with budget parameter — budget walk + progressive batches."""

    async def test_budget_none_keeps_one_turn(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a2"}, {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a3"}, {"role": "user", "content": "q3"},
        ]
        result, event = await pipe._compress(messages, budget=None)

        assert result[0]["role"] == "system"
        assert result[-1]["content"] == "q3"
        # system + synthetic(1) + keep turn(2) = 4
        assert len(result) == 4

    async def test_budget_fits_all_turns_returns_unchanged(self):
        pipe = MessagePipe()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a2"}, {"role": "user", "content": "q2"},
        ]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=10):
            result, event = await pipe._compress(messages, budget=100)
        # 2 turns × 20 tokens = 40 ≤ budget 100 → unchanged
        assert result == messages


    async def test_budget_walk_keeps_limited_turns(self):
        """Budget-based walk: keep 2 of 4 turns, compress the rest."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [{"role": "system", "content": "sys"}]
        for i in range(4):
            messages.append({"role": "assistant", "content": f"a{i}"})
            messages.append({"role": "user", "content": f"q{i}"})

        # Each msg = 10 tokens, each turn = 20 tokens, 4 turns = 80
        # budget=50 → keep 2 turns (40 tokens), compress 2 turns
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=10):
            result, event = await pipe._compress(messages, budget=50)

        assert result[0]["role"] == "system"
        assert result[-1]["content"] == "q3"
        # system + synthetic(1) + 2 keep turns(4) = 6
        assert len(result) == 6
        assert provider.chat_stream_with_retry.await_count == 1

    async def test_progressive_multi_batch(self):
        """> COMPRESS_BATCH_SIZE turns to compress → spans multiple progressive batches."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        from nanobot.agent.compress import COMPRESS_BATCH_SIZE

        total = COMPRESS_BATCH_SIZE * 2 + 20  # 120 turns → 119 to_compress → 3 batches
        messages = [{"role": "system", "content": "sys"}]
        for i in range(total):
            messages.append({"role": "assistant", "content": f"a{i}"})
            messages.append({"role": "user", "content": f"q{i}"})

        # Each turn = 200 tokens (2×100), budget=50 → keep only 1 turn
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=100):
            result, event = await pipe._compress(messages, budget=50)

        assert result[0]["role"] == "system"
        assert result[-1]["content"] == f"q{total - 1}"
        assert result[-1]["role"] == "user"
        # system + synthetic(1) + keep(2) = 4
        assert len(result) == 4
        # 3 batches → 3 compress_turns calls → 3 chat_stream_with_retry calls
        assert provider.chat_stream_with_retry.await_count == 3


class TestCompleteReturnsCompressed:
    """complete / complete_stream return compressed messages after overflow."""

    async def test_complete_returns_none_when_no_overflow(self):
        """No overflow → compress_event is None."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_success_response("ok"))
        provider.chat_stream_with_retry = AsyncMock()
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )
        assert event is None

    async def test_complete_stream_returns_none_when_no_overflow(self):
        """No overflow → compress_event is None (streaming path)."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("ok"))
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )
        assert event is None

    async def test_complete_returns_compressed_after_overflow_retry(self):
        """Overflow → compress → retry success → event is not None."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("retried"),
        ])
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        response, event = await pipe.complete(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
            model="test-model",
        )
        assert response.content == "retried"
        assert event is not None
        assert event.compressed_messages is not None
        # System prompt preserved
        assert event.compressed_messages[0]["role"] == "system"
        # Latest user message preserved
        assert event.compressed_messages[-1]["role"] == "user"

    async def test_complete_stream_returns_compressed_after_overflow_retry(self):
        """Overflow → compress → retry success → event is not None (streaming)."""
        pipe = MessagePipe()
        provider = MagicMock()
        call_count = 0

        async def _stream_fn(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_overflow_response()
            return _make_success_response("retried stream")

        provider.chat_stream_with_retry = AsyncMock(side_effect=_stream_fn)
        llm_set_llm(provider, "test-model")

        response, event = await pipe.complete_stream(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
            model="test-model",
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )
        assert response.content == "retried stream"
        assert event is not None
        assert event.compressed_messages is not None
        # System prompt preserved
        assert event.compressed_messages[0]["role"] == "system"
        # Latest user message preserved
        assert event.compressed_messages[-1]["role"] == "user"


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


# ===========================================================================
# MessagePipe._compress — additional edge cases
# ===========================================================================

class TestCompressEdgeCases:
    """``_compress`` edge cases not covered by main tests."""

    async def test_single_turn_drops_messages(self):
        """Only 1 turn after system → log warning and drop to system + latest."""
        pipe = MessagePipe()
        # history_msgs = [assistant:a1, user:q1, user:extra] → 1 turn
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q1"},
            {"role": "user", "content": "extra"},
        ]
        result, event = await pipe._compress(messages)
        # Single-turn branch: returns [system, latest]
        assert len(result) == 2
        assert result[0]["content"] == "sys"
        assert result[-1]["content"] == "extra"

    async def test_no_turns_to_compress_returns_unchanged(self):
        """All turns fit within budget → nothing to compress."""
        pipe = MessagePipe()
        # 2 turns, each 2 msgs, estimate=5/msg → 20 total, budget=100
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a2"}, {"role": "user", "content": "q2"},
        ]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=5):
            result, event = await pipe._compress(messages, budget=100)
        assert result == messages

    async def test_last_message_not_user_no_duplicate(self):
        """Last message is assistant → _compress doesn't try to append it again."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        result, event = await pipe._compress(messages)
        # No user message to re-append at the end
        assert result[-1]["role"] != "user" or result[-1]["content"] == "q1"
        # result[-1] is either a1 or the synthetic summary

    async def test_synthetic_pair_empty_fallback(self):
        """compress_turns returns failure → empty synthetic_pair → only system + keep."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]

        with patch("nanobot.agent.compress.compress_turns", return_value=(None, [])):
            result, event = await pipe._compress(messages)

        # No synthetic pair: only system + keep turns
        assert result[0]["role"] == "system"
        assert len(result) > 0

    async def test_last_user_already_in_result_no_duplicate(self):
        """User message is already the last entry in keep → no duplicate append."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_success_response("summary"))
        llm_set_llm(provider, "test-model")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q1"},  # this will be kept as last turn
        ]

        result, event = await pipe._compress(messages)
        # The last user message should appear exactly once
        user_messages = [m for m in result if m.get("role") == "user"]
        assert len(user_messages) == 1
        assert user_messages[0]["content"] == "q1"


# ===========================================================================
# MessagePipe final fallback overflow
# ===========================================================================

class TestFinalFallbackOverflow:
    """Last-attempt call itself overflows."""

    async def test_complete_last_attempt_overflow(self):
        """All MAX_RETRIES+1 calls overflow → last response is still overflow."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_make_overflow_response())
        llm_set_llm(provider, "test-model")

        result, compressed = await pipe.complete(
            messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            model="test-model",
        )

        assert _is_overflow(result)
        assert compressed is not None
        # MAX_RETRIES(3) + 1 compression + 1 final fallback = 5
        assert provider.chat_with_retry.await_count == 5

    async def test_complete_stream_last_attempt_overflow(self):
        """All calls overflow in streaming path."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_stream_with_retry = AsyncMock(return_value=_make_overflow_response())
        llm_set_llm(provider, "test-model")

        result, compressed = await pipe.complete_stream(
            messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            model="test-model",
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )

        assert _is_overflow(result)
        assert compressed is not None


# ===========================================================================
# MessagePipe budget passthrough
# ===========================================================================

class TestBudgetPassthrough:
    """budget parameter propagates from complete/complete_stream to _compress."""

    async def test_complete_passes_budget_to_compress(self):
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _make_overflow_response(),
            _make_success_response("ok"),
        ])
        provider.chat_stream_with_retry = AsyncMock()
        llm_set_llm(provider, "test-model")

        with patch.object(pipe, "_compress") as mock_compress:
            mock_compress.return_value = (
                [{"role": "user", "content": "compressed"}],
                CompressEvent(summary="s"),
            )
            await pipe.complete(
                messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
                model="test-model",
                budget=42,
            )

        _args, kwargs = mock_compress.call_args
        assert kwargs.get("budget") == 42
