"""Pure compression engine — no DB, no Session dependencies.

``Compressor`` provides static methods for turn splitting, budget-aware
selection, and batch summarisation.  Output is a ``CompressEvent`` dataclass
that callers use to persist replaced messages and sync state.

Public API
----------
``Compressor.split_turns(messages)``
    Split flat messages into user/assistant exchange turns.

``Compressor.split_by_budget(turns, budget, min_keep)``
    Pure split — return (to_compress, to_keep) turn lists by token budget.

``Compressor.compress(to_compress_turns, keep_turns, ...)``
    Batch-compress old turns → CompressEvent (async).

``CompressEvent``
    Dataclass carrying summary, synthetic_pair, replaced_raw,
    compressed_messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger


@dataclass
class CompressEvent:
    """Describes a compression operation outcome — pure data, no behavior.

    Callers use this to persist replaced messages and sync state.

    ``summary``
        The summarisation text (None if compression failed).
    ``synthetic_pair``
        Synthetic user message(s) to inject into the conversation.
    ``replaced_raw``
        The raw messages that were replaced (for history table persistence).
        Set by the compression caller, not by Compressor.compress itself.
    ``compressed_messages``
        For the MessagePipe/overflow path: the full compressed flat message
        list that the caller should sync back to ``messages[:]``.
    """
    summary: str | None = None
    synthetic_pair: list[dict] = field(default_factory=list)
    replaced_raw: list[dict] | None = None
    compressed_messages: list[dict] | None = None


class Compressor:
    """Pure compression engine — no DB, no Session dependencies."""

    # ------------------------------------------------------------------
    # Turn splitting
    # ------------------------------------------------------------------

    @staticmethod
    def split_turns(messages: list[dict]) -> list[list[dict]]:
        """Split flat messages into user/assistant exchange turns."""
        from nanobot.session.manager import Session
        return Session._split_turns_by_assistant(messages)

    # ------------------------------------------------------------------
    # Budget-aware split
    # ------------------------------------------------------------------

    @staticmethod
    def split_by_budget(
        turns: list[list[dict]],
        budget: int | None = None,
        min_keep: int = 1,
    ) -> tuple[list[list[dict]], list[list[dict]]]:
        """Split turns into ``(to_compress, to_keep)`` by token budget.

        Walks from the tail, accumulating tokens.  At least *min_keep*
        turns are always retained.  When *budget* is ``None`` only
        *min_keep* turns are kept and the rest go to *to_compress*.
        """
        from nanobot.utils.helpers import estimate_message_tokens

        if len(turns) <= min_keep:
            return [], turns

        if budget is None:
            return turns[:-min_keep], turns[-min_keep:]

        keep_start = len(turns)
        used = 0
        for i in range(len(turns) - 1, -1, -1):
            turn_tokens = sum(estimate_message_tokens(m) for m in turns[i])
            if keep_start < len(turns) and used + turn_tokens > budget:
                break
            used += turn_tokens
            keep_start = i
        keep_start = max(0, min(keep_start, len(turns) - min_keep))
        return turns[:keep_start], turns[keep_start:]

    # ------------------------------------------------------------------
    # Future context helper
    # ------------------------------------------------------------------

    @staticmethod
    def take_future_turns(
        all_turns: list[list[dict]],
        batch_start: int,
        batch_size: int,
        n_future: int,
        keep: list[list[dict]],
    ) -> list[dict]:
        """Take *n_future* turns after the current batch as context."""
        from nanobot.agent.compress import _take_future_turns
        return _take_future_turns(all_turns, batch_start, batch_size, n_future, keep)

    # ------------------------------------------------------------------
    # Batch compression (core)
    # ------------------------------------------------------------------

    @staticmethod
    async def compress(
        to_compress_turns: list[list[dict]],
        keep_turns: list[list[dict]],
        previous_summary: str | None = None,
        timestamp: str | None = None,
    ) -> CompressEvent:
        """Batch-compress old turns → ``CompressEvent``.

        Iterates batches of *to_compress_turns* (default batch size 50,
        controlled by ``COMPRESS_BATCH_SIZE`` in ``compress.py``),
        summarising each with future context from *keep_turns*.
        Progressive merging via *previous_summary*.

        Returns an empty ``CompressEvent`` when there is nothing to
        compress or when the first batch fails.
        """
        from nanobot.agent.compress import (
            COMPRESS_BATCH_SIZE,
            FUTURE_TURNS,
            compress_turns,
        )

        if not to_compress_turns:
            return CompressEvent()

        summary = None
        synthetic_pair: list[dict] = []
        n_total = len(to_compress_turns)
        n_batches = (n_total + COMPRESS_BATCH_SIZE - 1) // COMPRESS_BATCH_SIZE
        for batch_start in range(0, n_total, COMPRESS_BATCH_SIZE):
            chunk = to_compress_turns[batch_start:batch_start + COMPRESS_BATCH_SIZE]
            chunk_flat = [m for turn in chunk for m in turn]
            future_ctx = Compressor.take_future_turns(
                to_compress_turns, batch_start, len(chunk),
                FUTURE_TURNS, keep_turns,
            )
            batch_idx = batch_start // COMPRESS_BATCH_SIZE + 1
            logger.info(
                "CT_DBG: batch {}/{} — compressing {} turns ({} msgs), future_ctx={} msgs",
                batch_idx, n_batches, len(chunk), len(chunk_flat), len(future_ctx),
            )
            s, p = await compress_turns(
                chunk_flat, future_ctx,
                previous_summary=previous_summary,
                timestamp=timestamp,
            )
            logger.info("CT_DBG: batch {}/{} done (success={})", batch_idx, n_batches, bool(p))
            if not p:
                if summary is None:
                    return CompressEvent()
                break
            previous_summary = s
            summary = s
            synthetic_pair = p

        return CompressEvent(summary=summary, synthetic_pair=synthetic_pair)

    # ------------------------------------------------------------------
    # Synthetic pair factory
    # ------------------------------------------------------------------

    @staticmethod
    def make_summary_pair(summary: str, timestamp: str | None = None) -> list[dict]:
        from nanobot.agent.compress import make_summary_pair
        return make_summary_pair(summary, timestamp)
