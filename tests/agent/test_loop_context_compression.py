"""Tests for session context compression via LLM summarization."""

from __future__ import annotations

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
    loop._context_max_turns = defaults.context_max_turns
    loop._context_trim_batch = defaults.context_trim_batch
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


class TestFinalizeTurnCompression:
    """_finalize_turn injects summary pair when session turns >= max_turns."""

    async def test_injects_summary_pair_when_over_threshold(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        lifecycle = MagicMock()
        loop.lifecycle = lifecycle
        lifecycle.finalize.return_value = []
        loop._summarize_turns = AsyncMock(return_value="compressed context")
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        session = Session(key="test:compression")
        for i in range(80):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        loop._summarize_turns.assert_awaited_once()
        # Verify future_context was passed (remaining 60 turns)
        call_kwargs = loop._summarize_turns.call_args[1]
        call_args = loop._summarize_turns.call_args[0]
        if call_kwargs.get("future_context") is not None:
            assert len(call_kwargs["future_context"]) > 0
        elif len(call_args) > 1:
            assert len(call_args[1]) > 0  # future_context as positional
        # Summary pair injected at trim boundary
        # 80 user+assistant pairs → 81 turns (first user message is its own turn).
        # Boundary after 20 turns = 1 (turn0: user0) + 19×2 (turns1-19: asst+user) = 39
        assert session.messages[39]["role"] == "assistant"
        assert "compressed context" in session.messages[39]["content"]
        assert session.messages[40]["role"] == "user"
        assert session.messages[40]["content"] == "ok"

    async def test_skips_when_below_threshold(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        lifecycle = MagicMock()
        loop.lifecycle = lifecycle
        lifecycle.finalize.return_value = []
        loop._summarize_turns = AsyncMock()
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        session = Session(key="test:nocompress")
        for i in range(5):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        loop._summarize_turns.assert_not_called()

    async def test_empty_summary_skips_injection(self, tmp_path):
        loop, handler = _make_handler(tmp_path)
        lifecycle = MagicMock()
        loop.lifecycle = lifecycle
        lifecycle.finalize.return_value = []
        loop._summarize_turns = AsyncMock(return_value="")
        loop._append_turn_to_session = MagicMock()
        loop.context = MagicMock()
        loop.prompts_dir = tmp_path / "prompts"
        loop.prompts_dir.mkdir()

        session = Session(key="test:emptysummary")
        for i in range(80):
            session.messages.append({"role": "user", "content": f"msg {i}"})
            session.messages.append({"role": "assistant", "content": f"resp {i}"})

        await handler._finalize_turn(
            session, [], initial_msgs_count=1,
            user_persisted_early=False, final_content="ok",
        )

        loop._summarize_turns.assert_awaited_once()
        assert len(session.messages) == 160  # unchanged — no injection
