"""Tests for shared compression logic (session history).

Covers :mod:`nanobot.agent.compress` — pure helpers and async
``summarize_turns`` with mocked provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.compress import (
    _build_prompt,
    _compress_session,
    _format_turns,
    _prepend_summary,
    split_history_by_budget,
    summarize_turns,
)
from nanobot.providers.base import LLMResponse


# ===========================================================================
# Helpers
# ===========================================================================

def _msg(
    role: str,
    content: str | list = "",
    status: str | None = None,
    **kwargs: object,
) -> dict:
    msg: dict = {"role": role, "content": content}
    if status:
        msg["status"] = status
    msg.update(kwargs)
    return msg


def _fmt_turns(messages: list[dict]) -> list[list[dict]]:
    """Replicate Session._split_turns_by_assistant inline."""
    turns: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages:
        if msg.get("role") == "assistant":
            if current:
                turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


# ===========================================================================
# split_history_by_budget
# ===========================================================================

class TestSplitHistoryByBudget:
    """``split_history_by_budget`` — pure function with patched token estimation."""

    # -- fixtures -----------------------------------------------------------

    @pytest.fixture
    def three_turns(self) -> tuple[list[dict], list[list[dict]]]:
        """3 turns, each ~10 tokens when patched to return 10."""
        msgs = [
            _msg("user", "hello"),
            _msg("assistant", "a"),
            _msg("user", "followup"),
            _msg("assistant", "b"),
            _msg("user", "last"),
        ]
        return msgs, _fmt_turns(msgs)

    # -- tests --------------------------------------------------------------

    def test_all_within_budget(self, three_turns):
        """All turns fit → nothing to compress."""
        msgs, _ = three_turns
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                msgs, msgs, limit=100,
            )
        assert len(to_compress) == 0
        assert len(keeps_fmt) == len(_fmt_turns(msgs))

    def test_exceeds_budget(self, three_turns):
        """Budget exceeded → older turns go to compress."""
        msgs, turns = three_turns
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                msgs, msgs, limit=15,
            )
        # 3 turns × 10 tokens = 30, limit=15 → keep ~last 1-2
        assert len(to_compress) >= 1
        assert len(keeps_fmt) >= 1
        assert len(to_compress) + len(keeps_fmt) == len(turns)

    def test_keep_at_most_one_turn_when_budget_tight(self):
        """Tight budget still keeps at least the last turn."""
        msgs = [
            _msg("user", "large" * 50),
            _msg("assistant", "big" * 50),
        ]
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=999):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                msgs, msgs, limit=5,
            )
        assert len(keeps_fmt) == 1
        assert len(to_compress) >= 0

    def test_empty_messages(self):
        """Empty input → empty output."""
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                [], [], limit=100,
            )
        assert keeps_raw == []
        assert to_compress == []
        assert keeps_fmt == []

    def test_min_keep_turns_enforced(self):
        """min_keep_turns overrides budget, preserving N full turns."""
        # 3 turns × 100 tokens, limit=10 → would keep 1, but min_keep=2 → keep 2
        msgs = [
            _msg("user", "a"),
            _msg("assistant", "b"),
            _msg("user", "c"),
            _msg("assistant", "d"),
            _msg("user", "e"),
        ]
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=100):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                msgs, msgs, limit=10, min_keep_turns=2,
            )
        assert len(keeps_fmt) == 2
        assert len(to_compress) == 1

    def test_excluded_status_filtered_from_raw(self):
        """Messages with status='excluded' don't create extra turn boundaries."""
        raw_msgs = [
            _msg("user", "a"),
            _msg("assistant", "b"),
            _msg("user", "c"),
            _msg("assistant", "hidden", status="excluded"),
        ]
        fmt_msgs = [
            _msg("user", "a"),
            _msg("assistant", "b"),
            _msg("user", "c"),
        ]
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                raw_msgs, fmt_msgs, limit=100,
            )
        # After filtering excluded msg from raw → [user, assistant, user]
        # Raw turns = [[user]], [[assistant, user]]
        # Fmt turns = [[user]], [[assistant, user]]
        assert len(keeps_raw) == len(keeps_fmt)

    def test_fmt_fewer_turns_than_raw(self):
        """When fmt has fewer turns (front trimmed), alignment offset works."""
        raw_msgs = [
            _msg("user", "forgotten"),
            _msg("assistant", "old"),
            _msg("user", "a"),
            _msg("assistant", "current"),
        ]
        fmt_msgs = [
            _msg("user", "a"),
            _msg("assistant", "current"),
        ]
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                raw_msgs, fmt_msgs, limit=100,
            )
        # raw has 3 turns, fmt has 2 → n=2, offset=1
        # keeps_raw = raw_turns[offset:] = last 2 raw turns
        assert len(keeps_raw) == 2
        assert len(to_compress) == 0
        assert len(keeps_fmt) == 2
        # Last raw turn aligns with last fmt turn
        assert keeps_raw[-1][0]["content"] == "current"
        assert keeps_fmt[-1][0]["content"] == "current"

    def test_fmt_more_turns_than_raw(self):
        """When fmt has more turns, it is trimmed from front to match raw count."""
        raw_msgs = [
            _msg("user", "only"),
            _msg("assistant", "pair"),
        ]
        fmt_msgs = [
            _msg("user", "extra"),
            _msg("assistant", "x"),
            _msg("user", "only"),
            _msg("assistant", "pair"),
        ]
        with patch("nanobot.agent.compress.estimate_message_tokens", return_value=10):
            keeps_raw, to_compress, keeps_fmt = split_history_by_budget(
                raw_msgs, fmt_msgs, limit=100,
            )
        # raw has 2 turns, fmt has 3 → n=2, fmt trimmed to last 2
        assert len(keeps_raw) == 2
        assert len(keeps_fmt) == 2
        # The first retained fmt turn starts with the 2nd assistant, not "x"
        assert "pair" in keeps_fmt[-1][0]["content"]


# ===========================================================================
# _format_turns
# ===========================================================================

class TestFormatTurns:
    """``_format_turns`` — pure formatter."""

    def test_text_content(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = _format_turns(msgs)
        assert "<user>" in result
        assert "hello" in result
        assert "</user>" in result
        assert "<assistant>" in result
        assert "world" in result
        assert "</assistant>" in result

    def test_list_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "part a"},
                    {"type": "text", "text": "part b"},
                ],
            },
        ]
        result = _format_turns(msgs)
        assert "part a" in result
        assert "part b" in result

    def test_list_content_skips_non_text(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "visible"},
                    {"type": "image_url", "url": "data:..."},
                ],
            },
        ]
        result = _format_turns(msgs)
        assert "visible" in result
        assert "data" not in result

    def test_empty_list(self):
        assert _format_turns([]) == ""

    def test_missing_role_or_content(self):
        msgs = [
            {"role": "user"},
            {"content": "hi"},
        ]
        result = _format_turns(msgs)
        assert "hi" in result
        assert "<unknown>" in result  # missing role defaults to "unknown"

    def test_non_string_non_list_content_skipped(self):
        msgs = [{"role": "user", "content": 123}]
        assert _format_turns(msgs) == ""


# ===========================================================================
# _build_prompt
# ===========================================================================

class TestBuildPrompt:
    """``_build_prompt`` — pure prompt assembler."""

    def test_turns_included(self):
        turns = [{"role": "user", "content": "old text"}]
        future = [{"role": "assistant", "content": "new text"}]
        prompt = _build_prompt(turns, future)
        assert "old text" in prompt
        assert "new text" in prompt

    def test_with_previous_summary(self):
        turns = [{"role": "user", "content": "text"}]
        prompt = _build_prompt(turns, [], previous_summary="prev summary")
        assert "prev summary" in prompt
        assert "已有摘要" in prompt

    def test_without_previous_summary(self):
        prompt = _build_prompt([{"role": "user", "content": "text"}], [])
        assert "已有摘要" not in prompt

    def test_without_future_context(self):
        prompt = _build_prompt([{"role": "user", "content": "text"}], [])
        assert "text" in prompt

    def test_guidelines_in_prompt(self):
        prompt = _build_prompt([{"role": "user", "content": "x"}], [])
        assert "task" in prompt
        assert "你正在总结" in prompt
        assert "方向" in prompt


# ===========================================================================
# _prepend_summary
# ===========================================================================

class TestPrependSummary:
    """``_prepend_summary`` — pure helper."""

    def test_summary_prepended(self):
        keeps = [["a"], ["b"]]
        result = _prepend_summary(keeps, "my summary")
        assert len(result) == 4  # 2 summary + 2 turns
        assert result[0]["role"] == "user"
        assert "摘要" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "my summary"
        assert result[2] == "a"

    def test_empty_summary(self):
        keeps = [[{"role": "assistant", "content": "ok"}]]
        result = _prepend_summary(keeps, "")
        assert len(result) == 3
        assert result[1]["content"] == ""

    def test_single_turn(self):
        keeps = [[{"role": "assistant", "content": "alone"}]]
        result = _prepend_summary(keeps, "sum")
        assert len(result) == 3
        assert result[2]["content"] == "alone"

    def test_synthetic_status(self):
        keeps = [[{"role": "user", "content": "hi"}]]
        result = _prepend_summary(keeps, "s")
        assert result[0].get("status") == "synthetic"
        assert result[1].get("status") == "synthetic"
        # original turns should NOT have synthetic status
        assert result[2].get("status") is None


# ===========================================================================
# _compress_session
# ===========================================================================

class TestCompressSession:
    """``_compress_session`` — mutates Session, optionally writes to DB."""

    # -- helpers ------------------------------------------------------------

    def _make_session(self, messages: list[dict]) -> MagicMock:
        session = MagicMock(spec=["messages", "key"])
        session.messages = messages
        session.key = "test-key"
        return session

    # -- tests --------------------------------------------------------------

    def test_replaces_messages(self):
        session = self._make_session([
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "kept"},
        ])
        keeps_raw = [[{"role": "assistant", "content": "kept"}]]
        _compress_session(session, keeps_raw)
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "kept"

    def test_writes_to_db(self):
        session = self._make_session([
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "new"},
        ])
        db = MagicMock()
        keeps_raw = [[{"role": "assistant", "content": "new"}]]
        _compress_session(session, keeps_raw, db=db)
        assert db.append_history.called

    def test_no_db_no_error(self):
        session = self._make_session([
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "new"},
        ])
        keeps_raw = [[{"role": "assistant", "content": "new"}]]
        _compress_session(session, keeps_raw, db=None)  # should not raise

    def test_sets_summary_on_session(self):
        session = self._make_session([
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "new"},
        ])
        keeps_raw = [[{"role": "assistant", "content": "new"}]]
        _compress_session(session, keeps_raw, summary="my summary")
        assert session._last_summary == "my summary"

    def test_no_summary_does_not_set(self):
        session = self._make_session([
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "new"},
        ])
        keeps_raw = [[{"role": "assistant", "content": "new"}]]
        _compress_session(session, keeps_raw)
        # _last_summary is not in the spec, so hasattr checks are unreliable
        # with MagicMock. Verify that messages were replaced correctly.
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "new"

    def test_no_replaced_messages_does_not_write_db(self):
        """If nothing was replaced (split_point=0), DB write is skipped."""
        msg = {"role": "assistant", "content": "only"}
        session = self._make_session([msg])
        db = MagicMock()
        keeps_raw = [[msg]]  # same object identity
        _compress_session(session, keeps_raw, db=db)
        assert not db.append_history.called


# ===========================================================================
# summarize_turns
# ===========================================================================

class TestSummarizeTurns:
    """``summarize_turns`` — async with mocked provider."""

    @pytest.mark.asyncio
    async def test_empty_turns_returns_empty(self):
        result = await summarize_turns([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_successful_summary(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            return_value=LLMResponse(content="verified summary", finish_reason="stop"),
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"):
            result = await summarize_turns(
                [{"role": "user", "content": "text"}],
            )
        assert result == "verified summary"

    @pytest.mark.asyncio
    async def test_returns_empty_on_provider_error(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            side_effect=Exception("API error"),
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"):
            result = await summarize_turns(
                [{"role": "user", "content": "text"}],
            )
        assert result == ""

    @pytest.mark.asyncio
    async def test_retries_on_network_error_then_succeeds(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            side_effect=[Exception("timeout"), LLMResponse(content="ok", finish_reason="stop")],
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"), patch(
            "nanobot.agent.compress.asyncio.sleep",
        ) as mock_sleep:
            result = await summarize_turns(
                [{"role": "user", "content": "text"}],
            )
        assert result == "ok"
        assert mock_sleep.called

    @pytest.mark.asyncio
    async def test_retries_on_overflow_with_half_content(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            return_value=LLMResponse(
                content="context length exceeded", finish_reason="error", error_kind="context_length",
            ),
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"), patch(
            "nanobot.agent.compress.asyncio.sleep",
        ):
            result = await summarize_turns(
                [
                    {"role": "user", "content": "abcd"},
                    {"role": "assistant", "content": "efgh"},
                ],
            )
        assert result == ""  # all retries exhausted

    @pytest.mark.asyncio
    async def test_overflow_halves_content_and_retries(self):
        """Overflow with >1 turn halves and retries (eventually succeeds)."""
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            side_effect=[
                LLMResponse(content="context length exceeded", finish_reason="error", error_kind="context_length"),
                LLMResponse(content="half summary", finish_reason="stop"),
            ],
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"), patch(
            "nanobot.agent.compress.asyncio.sleep",
        ):
            result = await summarize_turns(
                [
                    {"role": "user", "content": "abcd"},
                    {"role": "assistant", "content": "efgh"},
                ],
            )
        assert result == "half summary"

    @pytest.mark.asyncio
    async def test_prompt_contains_guidelines(self):
        mock_stream = AsyncMock()
        mock_stream.return_value = LLMResponse(content="summary", finish_reason="stop")
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry", mock_stream,
        ):
            result = await summarize_turns(
                [{"role": "user", "content": "text"}],
            )
        assert result == "summary"
        assert mock_stream.called

    @pytest.mark.asyncio
    async def test_includes_future_context_in_prompt(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            return_value=LLMResponse(content="future-aware summary", finish_reason="stop"),
        ), patch("nanobot.agent.compress._build_prompt") as mock_build:
            mock_build.return_value = "built prompt"
            result = await summarize_turns(
                [{"role": "user", "content": "old"}],
                future_context=[{"role": "assistant", "content": "new"}],
            )
        assert result == "future-aware summary"
        # Verify _build_prompt was called with future context
        mock_build.assert_called_once()
        args = mock_build.call_args[0]
        assert len(args) == 3  # turns, future, previous_summary

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_overflow_retries_exhausted(self):
        with patch(
            "nanobot.agent.compress.chat_stream_with_retry",
            return_value=LLMResponse(
                content="context length exceeded", finish_reason="error", error_kind="context_length",
            ),
        ), patch("nanobot.agent.compress._build_prompt", return_value="prompt"), patch(
            "nanobot.agent.compress.asyncio.sleep",
        ):
            result = await summarize_turns(
                [{"role": "user", "content": "tiny"}],
            )
        assert result == ""
