"""Tests for runner_llm — LLM request/response with budget propagation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.runner_llm import request_finalization_retry, request_model
from nanobot.providers.base import LLMResponse


def _make_spec(*, compress_trigger_tokens: int | None = None,
               history_token_limit: int = 50_000) -> MagicMock:
    spec = MagicMock()
    spec.compress_trigger_tokens = compress_trigger_tokens
    spec.history_token_limit = history_token_limit
    spec.model = "test-model"
    spec.llm_timeout_s = None
    spec.temperature = None
    spec.max_tokens = None
    spec.reasoning_effort = None
    spec.provider_retry_mode = "standard"
    spec.retry_wait_callback = None
    spec.previous_summary = None
    spec.tools.get_definitions.return_value = []
    return spec


class TestRequestModelBudget:
    """budget passed to _message_pipe should use compress_trigger_tokens."""

    @pytest.mark.asyncio
    async def test_uses_compress_trigger_tokens_when_available(self):
        spec = _make_spec(compress_trigger_tokens=100_000, history_token_limit=50_000)
        pipe = AsyncMock()
        pipe.complete_stream.return_value = (
            LLMResponse(content="ok", finish_reason="stop"),
            None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("nanobot.agent.runner_llm._message_pipe", pipe)
            await request_model(spec, [{"role": "user", "content": "hi"}],
                                MagicMock(), MagicMock())

        pipe.complete_stream.assert_called_once()
        _budget = pipe.complete_stream.call_args.kwargs.get("budget")
        assert _budget == 100_000

    @pytest.mark.asyncio
    async def test_falls_back_to_history_token_limit(self):
        spec = _make_spec(compress_trigger_tokens=None, history_token_limit=60_000)
        pipe = AsyncMock()
        pipe.complete_stream.return_value = (
            LLMResponse(content="ok", finish_reason="stop"),
            None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("nanobot.agent.runner_llm._message_pipe", pipe)
            await request_model(spec, [{"role": "user", "content": "hi"}],
                                MagicMock(), MagicMock())

        pipe.complete_stream.assert_called_once()
        _budget = pipe.complete_stream.call_args.kwargs.get("budget")
        assert _budget == 60_000


class TestRequestFinalizationRetryBudget:
    """budget passed to _message_pipe for retry should use compress_trigger_tokens."""

    @pytest.mark.asyncio
    async def test_uses_compress_trigger_tokens_when_available(self):
        spec = _make_spec(compress_trigger_tokens=200_000, history_token_limit=80_000)
        pipe = AsyncMock()
        pipe.complete.return_value = (
            LLMResponse(content="ok", finish_reason="stop"),
            None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("nanobot.agent.runner_llm._message_pipe", pipe)
            await request_finalization_retry(spec, [{"role": "user", "content": "hi"}])

        pipe.complete.assert_called_once()
        _budget = pipe.complete.call_args.kwargs.get("budget")
        assert _budget == 200_000

    @pytest.mark.asyncio
    async def test_falls_back_to_history_token_limit(self):
        spec = _make_spec(compress_trigger_tokens=None, history_token_limit=70_000)
        pipe = AsyncMock()
        pipe.complete.return_value = (
            LLMResponse(content="ok", finish_reason="stop"),
            None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("nanobot.agent.runner_llm._message_pipe", pipe)
            await request_finalization_retry(spec, [{"role": "user", "content": "hi"}])

        pipe.complete.assert_called_once()
        _budget = pipe.complete.call_args.kwargs.get("budget")
        assert _budget == 70_000
