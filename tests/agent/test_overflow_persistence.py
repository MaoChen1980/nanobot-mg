"""Integration tests: overflow -> CompressEvent -> runner persistence.

Tests the end-to-end flow:
1. MessagePipe detects overflow -> compresses -> returns CompressEvent
2. Runner syncs compressed_messages back to messages
3. Runner persists replaced_raw to DB
"""

from __future__ import annotations

import json
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.compressor import CompressEvent
from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.agent.message_pipe import MessagePipe
from nanobot.agent.runner_llm import request_model, _message_pipe
from nanobot.providers.base import LLMResponse


def _overflow_resp(content: str = "context length exceeded") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="error", error_kind="context_length")


def _success_resp(content: str = "hello") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _make_spec(**overrides) -> MagicMock:
    """Create a minimal mock AgentRunSpec."""
    spec = MagicMock()
    spec.model = "test-model"
    spec.tools = MagicMock()
    spec.tools.get_definitions.return_value = []
    spec.history_token_limit = None
    spec.provider_retry_mode = "standard"
    spec.retry_wait_callback = None
    spec.temperature = None
    spec.max_tokens = None
    spec.reasoning_effort = None
    spec.llm_timeout_s = None
    spec.checkpoint_callback = None
    spec.injection_callback = None
    spec.assess_me_callback = None
    spec.progress_callback = None
    spec.workspace = None
    spec.prompts_dir = None
    for k, v in overrides.items():
        setattr(spec, k, v)
    return spec


# ===========================================================================
# MessagePipe + CompressEvent integration
# ===========================================================================

class TestMessagePipeOverflowFlow:
    """MessagePipe returns CompressEvent after overflow."""

    @pytest.mark.asyncio
    async def test_overflow_triggers_compress_event(self):
        """MessagePipe.complete returns CompressEvent after overflow + retry."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _overflow_resp(),
            _success_resp("retried ok"),
        ])
        # _compress -> Compressor.compress -> summarize_turns needs provider
        provider.chat_stream_with_retry = AsyncMock(
            return_value=LLMResponse(content="summary text", finish_reason="stop"),
        )
        llm_set_llm(provider, "test-model")

        response, event = await pipe.complete(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "old q"},
                {"role": "assistant", "content": "old a"},
                {"role": "user", "content": "new q"},
            ],
            model="test-model",
        )

        assert response.content == "retried ok"
        assert event is not None
        assert event.summary is not None
        assert event.replaced_raw is not None
        assert event.compressed_messages is not None

    @pytest.mark.asyncio
    async def test_compress_event_contains_replaced_raw(self):
        """CompressEvent.replaced_raw captures the exact messages removed."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _overflow_resp(),
            _success_resp("retried"),
        ])
        provider.chat_stream_with_retry = AsyncMock(
            return_value=LLMResponse(content="summary", finish_reason="stop"),
        )
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "turn1 q"},
                {"role": "assistant", "content": "turn1 a"},
                {"role": "user", "content": "current q"},
            ],
            model="test-model",
        )

        assert event is not None
        # budget=None compresses all but the last turn → 1 message replaced
        assert len(event.replaced_raw) >= 1
        assert event.replaced_raw[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_compressed_messages_structure(self):
        """compressed_messages is ready for runner to sync back."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=[
            _overflow_resp(),
            _success_resp("retried"),
        ])
        provider.chat_stream_with_retry = AsyncMock(
            return_value=LLMResponse(content="summary", finish_reason="stop"),
        )
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "turn1 q"},
                {"role": "assistant", "content": "turn1 a"},
                {"role": "user", "content": "current q"},
            ],
            model="test-model",
        )

        assert event is not None
        assert event.compressed_messages is not None
        assert event.compressed_messages[0]["role"] == "system"
        assert event.compressed_messages[1].get("status") == "synthetic"
        assert event.compressed_messages[-1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_no_overflow_no_compress_event(self):
        """No overflow -> compress_event is None."""
        pipe = MessagePipe()
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=_success_resp("ok"))
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )

        assert event is None

    @pytest.mark.asyncio
    async def test_overflow_stream_path_returns_compress_event(self):
        """complete_stream also returns CompressEvent after overflow."""
        pipe = MessagePipe()
        provider = MagicMock()
        call_count = 0

        async def _stream_fn(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _overflow_resp()
            return _success_resp("retried stream")

        provider.chat_stream_with_retry = AsyncMock(side_effect=_stream_fn)
        # complete_stream needs chat_with_retry for the overflow +
        # _compress -> summarize_turns path
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(content="summary text", finish_reason="stop"),
        )
        llm_set_llm(provider, "test-model")

        _, event = await pipe.complete_stream(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"},
                {"role": "user", "content": "followup"},
            ],
            model="test-model",
            on_content_delta=AsyncMock(),
            on_reasoning_delta=AsyncMock(),
        )

        assert event is not None
        assert event.summary is not None
        assert event.replaced_raw is not None
        assert event.compressed_messages is not None


# ===========================================================================
# Runner persistence of CompressEvent
# ===========================================================================

class TestRunnerPersistence:
    """AgentRunner persists CompressEvent correctly."""

    @pytest.mark.asyncio
    async def test_runner_syncs_compressed_messages(self):
        """Runner syncs compressed_messages back into messages list."""
        spec = _make_spec(initial_messages=[{"role": "user", "content": "hi"}])
        messages = [{"role": "user", "content": "hi"}]
        hook = MagicMock()
        hook.on_stream = AsyncMock()
        hook.on_reasoning = AsyncMock()
        context = MagicMock()

        compressed_msgs = [
            {"role": "user", "content": "summary", "status": "synthetic"},
        ]

        with patch.object(_message_pipe, "complete_stream", AsyncMock(return_value=(
            _success_resp("hello"),
            CompressEvent(
                summary="summary",
                synthetic_pair=[{"role": "user", "content": "summary", "status": "synthetic"}],
                replaced_raw=[{"role": "user", "content": "old"}],
                compressed_messages=compressed_msgs,
            ),
        ))):
            response, compress_event = await request_model(spec, messages, hook, context)

        assert compress_event is not None
        assert compress_event.compressed_messages == compressed_msgs
        assert compress_event.replaced_raw is not None

    @pytest.mark.asyncio
    async def test_runner_persists_replaced_raw_to_db(self):
        """Runner persists replaced_raw to db.append_history when db is set."""
        from nanobot.agent.runner import AgentRunner
        from nanobot.providers.base import LLMProvider

        provider = MagicMock(spec=LLMProvider)
        db = MagicMock()
        runner = AgentRunner(provider, db=db)

        spec = _make_spec(
            max_iterations=1,
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q"},
            ],
            session_key="test-session",
            hook=None,
        )

        with patch.object(_message_pipe, "complete_stream", AsyncMock(return_value=(
            _success_resp("hello"),
            CompressEvent(
                summary="my summary",
                replaced_raw=[{"role": "user", "content": "old q"}, {"role": "assistant", "content": "old a"}],
                compressed_messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "summary", "status": "synthetic"},
                    {"role": "user", "content": "q"},
                ],
            ),
        ))):
            result = await runner.run(spec)

        assert result.stop_reason == "completed"
        db.append_history.assert_called_once_with(
            content=ANY,
            summary="my summary",
        )

    @pytest.mark.asyncio
    async def test_runner_skips_db_when_db_none(self):
        """Runner does not crash when db is None and compress_event has replaced_raw."""
        from nanobot.agent.runner import AgentRunner
        from nanobot.providers.base import LLMProvider

        provider = MagicMock(spec=LLMProvider)
        runner = AgentRunner(provider, db=None)

        spec = _make_spec(
            max_iterations=1,
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q"},
            ],
            session_key="test-session",
            hook=None,
        )

        with patch.object(_message_pipe, "complete_stream", AsyncMock(return_value=(
            _success_resp("hello"),
            CompressEvent(
                summary="my summary",
                replaced_raw=[{"role": "user", "content": "old"}],
                compressed_messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "summary", "status": "synthetic"},
                ],
            ),
        ))):
            result = await runner.run(spec)

        assert result.stop_reason == "completed"

    @pytest.mark.asyncio
    async def test_runner_db_failure_does_not_crash(self):
        """append_history failure is caught and logged without breaking the loop."""
        from nanobot.agent.runner import AgentRunner
        from nanobot.providers.base import LLMProvider

        provider = MagicMock(spec=LLMProvider)
        db = MagicMock()
        db.append_history.side_effect = RuntimeError("DB disk full")
        runner = AgentRunner(provider, db=db)

        spec = _make_spec(
            max_iterations=1,
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q"},
            ],
            session_key="test-session",
            hook=None,
        )

        with patch.object(_message_pipe, "complete_stream", AsyncMock(return_value=(
            _success_resp("hello"),
            CompressEvent(
                summary="my summary",
                replaced_raw=[{"role": "user", "content": "old"}],
                compressed_messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "summary", "status": "synthetic"},
                ],
            ),
        ))):
            result = await runner.run(spec)

        assert result.stop_reason == "completed"
