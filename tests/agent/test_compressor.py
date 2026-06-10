"""Tests for Compressor — pure compression engine.

Covers :mod:`nanobot.agent.compressor` — ``CompressEvent`` dataclass and
``Compressor`` static methods in isolation (no DB, no Session).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.compressor import CompressEvent, Compressor
from nanobot.providers.base import LLMResponse


# ===========================================================================
# CompressEvent dataclass
# ===========================================================================

class TestCompressEvent:
    """``CompressEvent`` — pure data container."""

    def test_default_construction(self):
        event = CompressEvent()
        assert event.summary is None
        assert event.synthetic_pair == []
        assert event.replaced_raw is None
        assert event.compressed_messages is None

    def test_construction_with_fields(self):
        event = CompressEvent(
            summary="my summary",
            synthetic_pair=[{"role": "user", "content": "summary"}],
            replaced_raw=[{"role": "user", "content": "old"}],
            compressed_messages=[{"role": "user", "content": "summary"}],
        )
        assert event.summary == "my summary"
        assert len(event.synthetic_pair) == 1
        assert event.synthetic_pair[0]["content"] == "summary"
        assert len(event.replaced_raw) == 1  # type: ignore[arg-type]
        assert event.replaced_raw[0]["content"] == "old"  # type: ignore[index]
        assert event.compressed_messages[0]["content"] == "summary"  # type: ignore[index]

    def test_partial_fields(self):
        event = CompressEvent(summary="only summary")
        assert event.summary == "only summary"
        assert event.synthetic_pair == []
        assert event.replaced_raw is None
        assert event.compressed_messages is None

    def test_replaced_raw_can_be_empty_list(self):
        event = CompressEvent(replaced_raw=[])
        assert event.replaced_raw == []


# ===========================================================================
# Compressor.split_by_budget
# ===========================================================================

def _turn(role: str, content: str = "") -> list[dict]:
    """Helper to create a single-message turn."""
    return [{"role": role, "content": content}]


class TestSplitByBudget:
    """``Compressor.split_by_budget`` — pure split with patched token estimation."""

    def test_empty_turns(self):
        result = Compressor.split_by_budget([], budget=100)
        assert result == ([], [])

    def test_turns_less_than_min_keep(self):
        turns = [_turn("user", "a")]
        to_compress, to_keep = Compressor.split_by_budget(turns, budget=100)
        assert to_compress == []
        assert len(to_keep) == 1

    def test_exact_min_keep(self):
        turns = [_turn("user", "a"), _turn("assistant", "b")]
        to_compress, to_keep = Compressor.split_by_budget(turns, budget=100)
        assert to_compress == []
        assert len(to_keep) == 2

    def test_budget_fits_all(self):
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=5):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=100)
        assert to_compress == []
        assert len(to_keep) == 3

    def test_budget_exceeded_keeps_fewer(self):
        """3 turns × 10 tokens = 30, budget=15 → keep ~1 turn."""
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=10):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=15)
        assert len(to_compress) == 2
        assert len(to_keep) == 1

    def test_min_keep_overrides_budget(self):
        """3 turns × 100 tokens, budget=10 → would keep 0, min_keep=2 → keep 2."""
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=100):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=10, min_keep=2)
        assert len(to_compress) == 1
        assert len(to_keep) == 2

    def test_budget_none_keeps_only_min_keep(self):
        """budget=None → keep 1 turn, compress the rest."""
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]
        to_compress, to_keep = Compressor.split_by_budget(turns, budget=None)
        assert len(to_compress) == 2
        assert len(to_keep) == 1

    def test_budget_none_with_multiple_turns(self):
        """budget=None with 5 turns → keep 1."""
        turns = [_turn("user", str(i)) for i in range(5)]
        to_compress, to_keep = Compressor.split_by_budget(turns, budget=None)
        assert len(to_compress) == 4
        assert len(to_keep) == 1

    def test_budget_zero_keeps_one(self):
        """Even budget=0 keeps at least min_keep=1 turns."""
        turns = [_turn("user", "a"), _turn("assistant", "b")]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=100):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=0)
        assert len(to_compress) == 1
        assert len(to_keep) == 1

    def test_multi_message_turns(self):
        """Turns with multiple messages are counted correctly.
        Turn 0 has 2 msgs (30 tokens), turn 1 has 1 (15), turn 2 has 1 (15).
        Budget=20 -> keep 1 turn (15 tokens), compress 2 turns (45 tokens).
        """
        turns = [
            [{"role": "user", "content": "q1"}, {"role": "tool", "content": "r1"}],
            [{"role": "assistant", "content": "a1"}],
            [{"role": "user", "content": "q2"}],
        ]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=15):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=20)
        assert len(to_compress) == 2
        assert len(to_keep) == 1

    def test_preserves_turn_order(self):
        """Compressed turns come first in order, kept turns are the tail."""
        turns = [_turn("user", str(i)) for i in range(5)]
        with patch("nanobot.utils.helpers.estimate_message_tokens", return_value=10):
            to_compress, to_keep = Compressor.split_by_budget(turns, budget=12)
        # 5 turns × 10 = 50, budget=12 → keep 1
        assert to_compress == turns[:4]
        assert to_keep == turns[4:]


# ===========================================================================
# Compressor.compress  (batch orchestration)
# ===========================================================================

class TestCompress:
    """``Compressor.compress`` — async batch orchestration with mocked provider."""

    @pytest.mark.asyncio
    async def test_empty_turns_returns_empty_event(self):
        event = await Compressor.compress([], [])
        assert event.summary is None
        assert event.synthetic_pair == []

    @pytest.mark.asyncio
    async def test_single_batch(self):
        """Single batch of ≤ COMPRESS_BATCH_SIZE turns."""
        turns = [_turn("user", str(i)) for i in range(3)]
        with patch(
            "nanobot.agent.compress.compress_turns",
            return_value=("my summary", [{"role": "user", "content": "my summary", "status": "synthetic"}]),
        ):
            event = await Compressor.compress(turns, [])

        assert event.summary == "my summary"
        assert len(event.synthetic_pair) == 1
        assert event.synthetic_pair[0]["content"] == "my summary"

    @pytest.mark.asyncio
    async def test_first_batch_failure_returns_empty(self):
        """If the first batch fails, the whole compression is abandoned."""
        turns = [_turn("user", str(i)) for i in range(3)]
        with patch(
            "nanobot.agent.compress.compress_turns",
            return_value=(None, []),
        ):
            event = await Compressor.compress(turns, [])

        assert event.summary is None
        assert event.synthetic_pair == []

    @pytest.mark.asyncio
    async def test_second_batch_failure_returns_first_batch_result(self):
        """If the first batch succeeds but the second fails, return first batch result."""
        from nanobot.agent.compress import COMPRESS_BATCH_SIZE

        # Create enough turns for 2+ batches
        total = COMPRESS_BATCH_SIZE + 5
        turns = [_turn("user", str(i)) for i in range(total)]

        call_count = 0

        async def _compress_turns(to_compress, future_ctx, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "first batch summary", [{"role": "user", "content": "first"}]
            # second batch fails
            return None, []

        with patch(
            "nanobot.agent.compress.compress_turns",
            side_effect=_compress_turns,
        ):
            event = await Compressor.compress(turns, [])

        assert event.summary == "first batch summary"
        assert len(event.synthetic_pair) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_passes_previous_summary_to_subsequent_batches(self):
        """Each batch receives the previous batch's summary as previous_summary."""
        from nanobot.agent.compress import COMPRESS_BATCH_SIZE

        total = COMPRESS_BATCH_SIZE + 5
        turns = [_turn("user", str(i)) for i in range(total)]

        received_summaries = []

        async def _compress_turns(to_compress, future_ctx, **kwargs):
            received_summaries.append(kwargs.get("previous_summary"))
            return "batch summary", [{"role": "user", "content": "ok"}]

        with patch(
            "nanobot.agent.compress.compress_turns",
            side_effect=_compress_turns,
        ):
            event = await Compressor.compress(turns, [])

        # First call: previous_summary=None, Second call: previous_summary="batch summary"
        assert received_summaries[0] is None
        assert received_summaries[1] == "batch summary"
        assert event.summary == "batch summary"

    @pytest.mark.asyncio
    async def test_passes_timestamp_to_compress_turns(self):
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]
        with patch(
            "nanobot.agent.compress.compress_turns",
            return_value=("ts summary", [{"role": "user", "content": "ts"}]),
        ) as mock_fn:
            event = await Compressor.compress(turns, [], timestamp="2026-06-08T00:00:00")

        assert event.summary == "ts summary"
        mock_fn.assert_called_once()
        args, kwargs = mock_fn.call_args
        assert kwargs.get("timestamp") == "2026-06-08T00:00:00"

    @pytest.mark.asyncio
    async def test_multi_batch_progressive_merging(self):
        """Multiple batches → summaries flow progressively through previous_summary."""
        from nanobot.agent.compress import COMPRESS_BATCH_SIZE

        total = COMPRESS_BATCH_SIZE * 2 + 10
        turns = [_turn("user", str(i)) for i in range(total)]

        call_count = 0

        async def _compress_turns(to_compress, future_ctx, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"summary-{call_count}", [{"role": "user", "content": f"summary-{call_count}"}]

        with patch(
            "nanobot.agent.compress.compress_turns",
            side_effect=_compress_turns,
        ):
            event = await Compressor.compress(turns, [])

        # 3 batches expected
        assert call_count == 3
        assert event.summary == "summary-3"
        assert len(event.synthetic_pair) == 1

    @pytest.mark.asyncio
    async def test_with_initial_previous_summary(self):
        """previous_summary passed in is forwarded to the first batch."""
        turns = [_turn("user", "a"), _turn("assistant", "b"), _turn("user", "c")]

        with patch(
            "nanobot.agent.compress.compress_turns",
            return_value=("updated summary", [{"role": "user", "content": "updated"}]),
        ) as mock_fn:
            event = await Compressor.compress(turns, [], previous_summary="initial summary")

        assert event.summary == "updated summary"
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs.get("previous_summary") == "initial summary"
