"""Tests for session context compression via LLM summarization."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.agent.loop_message_handlers import UserMessageHandler
from nanobot.providers.base import LLMResponse
from nanobot.session.manager import Session


def _make_handler(workspace: Path | None = None) -> tuple[AgentLoop, UserMessageHandler]:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop.__new__(AgentLoop)
    from nanobot.config.schema import AgentDefaults
    defaults = AgentDefaults()
    loop.max_tool_result_chars = defaults.max_tool_result_chars
    loop.provider = provider
    loop.model = "test-model"
    loop.workspace = workspace or Path("/tmp")
    loop.extractor = MagicMock()
    loop._pt_save_interval = 30
    handler = UserMessageHandler(loop)
    return loop, handler


class TestSummarizeTurns:
    """_summarize_turns calls provider.chat() and returns the response content."""

    async def test_returns_summary(self):
        loop, _ = _make_handler()
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="key fact: 42"))
        turns = [
            {"role": "user", "content": "What is the answer?"},
            {"role": "assistant", "content": "The answer is 42."},
        ]
        summary = await loop._summarize_turns(turns)
        assert summary == "key fact: 42"
        loop.provider.chat.assert_awaited_once()
        call_args = loop.provider.chat.call_args[0][0]
        assert call_args[0]["role"] == "user"
        assert "The answer is 42." in call_args[0]["content"]

    async def test_handles_content_blocks(self):
        loop, _ = _make_handler()
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="summary ok"))
        turns = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "tool", "content": [{"type": "text", "text": "result: done"}]},
        ]
        summary = await loop._summarize_turns(turns)
        assert summary == "summary ok"

    async def test_returns_empty_on_provider_error(self):
        loop, _ = _make_handler()
        loop.provider.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        summary = await loop._summarize_turns([])
        assert summary == ""

    async def test_includes_future_context_in_prompt(self):
        loop, _ = _make_handler()
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="summary"))
        turns = [{"role": "user", "content": "old msg"}]
        future = [{"role": "assistant", "content": "future context"}]
        await loop._summarize_turns(turns, future)
        prompt = loop.provider.chat.call_args[0][0][0]["content"]
        assert "future context" in prompt
        assert "old msg" in prompt
        loop, _ = _make_handler()
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content=""))
        await loop._summarize_turns([])
        prompt = loop.provider.chat.call_args[0][0][0]["content"]
        assert "后面" in prompt  # references future context
        assert "方向（由你判断" in prompt  # guidelines, not rules
        assert "最重要" in prompt
        assert "参考" in prompt


class TestCompressIfNeeded:
    """_compress_if_needed trims session and starts bg summary when over budget."""

    async def test_trims_and_starts_bg_task(self):
        loop, _ = _make_handler()
        loop._last_adjusted_budget = 100
        loop._summarize_turns = AsyncMock(return_value="compressed context")
        loop.context = MagicMock()

        session = Session(key="test:compress")
        for i in range(80):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        original_len = len(session.messages)
        result = loop._compress_if_needed(session)

        assert result is True
        # Messages tagged, not deleted — data preserved
        assert len(session.messages) == original_len
        pending_count = sum(1 for m in session.messages if m.get("status") == "pending_compress")
        assert pending_count > 0  # oldest turns tagged
        assert pending_count < original_len  # but not all — future context preserved
        assert loop._pending_compression is not None

        # Background task should complete
        await asyncio.sleep(0)
        assert loop._pending_compression.done()
        assert loop._pending_compression.result() == "compressed context"

        # Content archived in _finalize_turn, not here
        loop.context.memory.condense_session_to_history.assert_not_called()

    async def test_skips_when_below_threshold(self):
        loop, _ = _make_handler()
        loop._last_adjusted_budget = 10000
        loop._summarize_turns = AsyncMock()
        loop.context = MagicMock()

        session = Session(key="test:nocompress")
        for i in range(5):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        result = loop._compress_if_needed(session)
        assert result is False
        loop._summarize_turns.assert_not_called()
        assert not hasattr(loop, '_pending_compression') or loop._pending_compression is None

    async def test_skips_when_pending_exists(self):
        """Don't start a new compression while one is already pending."""
        loop, _ = _make_handler()
        loop._last_adjusted_budget = 100
        loop._summarize_turns = AsyncMock()
        loop.context = MagicMock()

        pending = asyncio.Future()
        loop._pending_compression = pending

        session = Session(key="test:pending")
        for i in range(80):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        result = loop._compress_if_needed(session)
        assert result is False  # skipped — pending already exists
        loop._summarize_turns.assert_not_called()

    async def test_returns_false_when_no_budget_set(self):
        loop, _ = _make_handler()
        loop.context = MagicMock()

        session = Session(key="test:nobudget")
        result = loop._compress_if_needed(session)
        assert result is False

    async def test_preserves_old_summary_and_injects_trim(self):
        """Leading synthetic turns are counted but not included in 25% trim target."""
        loop, _ = _make_handler()
        loop._last_adjusted_budget = 100
        loop._summarize_turns = AsyncMock(return_value="merged summary")
        loop.context = MagicMock()

        session = Session(key="test:withsynth")
        # Pre-existing synthetic summary pair
        session.messages.append({"role": "assistant", "content": "old summary", "status": "synthetic"})
        session.messages.append({"role": "user", "content": "ok", "status": "synthetic"})
        # Some non-synthetic content that should be trimmed
        for i in range(20):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        result = loop._compress_if_needed(session)

        assert result is True
        # Old synthetic pair tagged as pending_compress (consolidated into new bg summary)
        assert session.messages[0]["status"] == "pending_compress"
        assert session.messages[1]["status"] == "pending_compress"


class TestFinalizeTurnAppliesPendingSummary:
    """_finalize_turn applies the completed background summary task."""

    async def test_applies_completed_summary(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        loop.lifecycle = MagicMock()
        loop.lifecycle.finalize.return_value = []
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        fut = asyncio.Future()
        fut.set_result("compressed context")
        loop._pending_compression = fut

        session = Session(key="test:apply")
        session.messages.append({"role": "assistant", "content": "old summary", "status": "pending_compress"})
        session.messages.append({"role": "user", "content": "ok", "status": "pending_compress"})
        session.messages.append({"role": "user", "content": "remaining msg", "status": None})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        # Old pending_compress replaced with new summary pair
        assert session.messages[0]["role"] == "assistant"
        assert "compressed context" in session.messages[0]["content"]
        assert session.messages[0].get("status") == "synthetic"
        assert session.messages[1]["role"] == "user"
        assert session.messages[1].get("status") == "synthetic"
        # Remaining messages preserved after summary pair
        assert len(session.messages) == 3
        assert session.messages[2]["content"] == "remaining msg"
        # Pending cleared
        assert loop._pending_compression is None

    async def test_skips_when_no_pending(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        loop.lifecycle = MagicMock()
        loop.lifecycle.finalize.return_value = []
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        session = Session(key="test:nopending")
        session.messages.append({"role": "user", "content": "hello"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        assert len(session.messages) == 1  # unchanged (append is mocked)

    async def test_empty_summary_skips_injection(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        loop.lifecycle = MagicMock()
        loop.lifecycle.finalize.return_value = []
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        fut = asyncio.Future()
        fut.set_result("")
        loop._pending_compression = fut

        session = Session(key="test:empty")
        session.messages.append({"role": "assistant", "content": "old summary", "status": "pending_compress"})
        session.messages.append({"role": "user", "content": "ok", "status": "pending_compress"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        # Empty summary — pending_compress flags cleared, messages preserved
        assert session.messages[0]["content"] == "old summary"
        assert session.messages[0].get("status") is None
        assert session.messages[1]["content"] == "ok"
        assert session.messages[1].get("status") is None
        # Pending cleared
        assert loop._pending_compression is None

    async def test_failed_task_clears_pending(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        loop.lifecycle = MagicMock()
        loop.lifecycle.finalize.return_value = []
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        fut = asyncio.Future()
        fut.set_exception(RuntimeError("LLM down"))
        loop._pending_compression = fut

        session = Session(key="test:fail")
        session.messages.append({"role": "assistant", "content": "old", "status": "pending_compress"})
        session.messages.append({"role": "user", "content": "ok", "status": "pending_compress"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        # Session unchanged, pending flags cleared — data preserved
        assert session.messages[0]["content"] == "old"
        assert session.messages[0].get("status") is None
        assert session.messages[1]["content"] == "ok"
        assert session.messages[1].get("status") is None
        assert len(session.messages) == 2
        # Pending cleared
        assert loop._pending_compression is None
