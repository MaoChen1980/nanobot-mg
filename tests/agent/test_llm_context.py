"""Tests for llm_context — unified LLM call interface via ContextVars."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.llm_context import (
    chat,
    chat_stream_with_retry,
    chat_with_retry,
    set_llm,
)
from nanobot.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# set_llm (indirect verification — check that subsequent chat() works)
# ---------------------------------------------------------------------------


class TestSetLlm:
    """set_llm must inject the provider+model into the module-level ContextVars."""

    @pytest.mark.asyncio
    async def test_sets_provider_and_model(self) -> None:
        provider = MagicMock()
        provider.chat_stream = AsyncMock(return_value=LLMResponse(content="ok"))
        set_llm(provider, "test-model")
        result = await chat([{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        provider.chat_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_can_overwrite(self) -> None:
        p1 = MagicMock()
        p2 = MagicMock()
        p2.chat_stream = AsyncMock(return_value=LLMResponse(content="ok"))
        set_llm(p1, "model-a")
        set_llm(p2, "model-b")
        result = await chat([{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        p2.chat_stream.assert_awaited_once()
        p1.chat_stream.assert_not_called()


# ---------------------------------------------------------------------------
# chat — delegates to provider.chat_stream
# ---------------------------------------------------------------------------


class TestChat:
    """chat() must call provider.chat_stream with correct args."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.provider = MagicMock()
        self.provider.chat_stream = AsyncMock(return_value=LLMResponse(content="ok"))
        set_llm(self.provider, "default-model")

    @pytest.mark.asyncio
    async def test_calls_chat_stream(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = await chat(msgs)
        assert result.content == "ok"
        self.provider.chat_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_messages(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        await chat(msgs)
        _, kwargs = self.provider.chat_stream.call_args
        assert kwargs["messages"] == msgs

    @pytest.mark.asyncio
    async def test_auto_injects_model(self) -> None:
        await chat([{"role": "user", "content": "hi"}])
        _, kwargs = self.provider.chat_stream.call_args
        assert kwargs["model"] == "default-model"

    @pytest.mark.asyncio
    async def test_model_override(self) -> None:
        await chat([{"role": "user", "content": "hi"}], model="other-model")
        _, kwargs = self.provider.chat_stream.call_args
        assert kwargs["model"] == "other-model"

    @pytest.mark.asyncio
    async def test_passes_extra_kwargs(self) -> None:
        await chat([{"role": "user", "content": "hi"}], max_tokens=512, temperature=0.5)
        _, kwargs = self.provider.chat_stream.call_args
        assert kwargs["max_tokens"] == 512
        assert kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_passes_tools(self) -> None:
        tools = [{"type": "function", "function": {"name": "read_file_tool"}}]
        await chat([{"role": "user", "content": "hi"}], tools=tools)
        _, kwargs = self.provider.chat_stream.call_args
        assert kwargs["tools"] == tools


# ---------------------------------------------------------------------------
# chat_stream_with_retry — delegates to provider.chat_stream_with_retry
# ---------------------------------------------------------------------------


class TestChatStreamWithRetry:
    """chat_stream_with_retry() must call provider.chat_stream_with_retry."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.provider = MagicMock()
        self.provider.chat_stream_with_retry = AsyncMock(
            return_value=LLMResponse(content="streamed")
        )
        set_llm(self.provider, "default-model")

    @pytest.mark.asyncio
    async def test_calls_chat_stream_with_retry(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = await chat_stream_with_retry(msgs)
        assert result.content == "streamed"
        self.provider.chat_stream_with_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_messages(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        await chat_stream_with_retry(msgs)
        _, kwargs = self.provider.chat_stream_with_retry.call_args
        assert kwargs["messages"] == msgs

    @pytest.mark.asyncio
    async def test_auto_injects_model(self) -> None:
        await chat_stream_with_retry([{"role": "user", "content": "hi"}])
        _, kwargs = self.provider.chat_stream_with_retry.call_args
        assert kwargs["model"] == "default-model"

    @pytest.mark.asyncio
    async def test_model_override(self) -> None:
        await chat_stream_with_retry([{"role": "user", "content": "hi"}], model="override")
        _, kwargs = self.provider.chat_stream_with_retry.call_args
        assert kwargs["model"] == "override"

    @pytest.mark.asyncio
    async def test_passes_on_content_delta(self) -> None:
        async def cb(delta: str) -> None:
            pass

        await chat_stream_with_retry([{"role": "user", "content": "hi"}], on_content_delta=cb)
        _, kwargs = self.provider.chat_stream_with_retry.call_args
        assert kwargs["on_content_delta"] is cb

    @pytest.mark.asyncio
    async def test_passes_tools(self) -> None:
        tools = [{"type": "function", "function": {"name": "read_file_tool"}}]
        await chat_stream_with_retry([{"role": "user", "content": "hi"}], tools=tools)
        _, kwargs = self.provider.chat_stream_with_retry.call_args
        assert kwargs["tools"] == tools


# ---------------------------------------------------------------------------
# chat_with_retry — delegates to provider.chat_with_retry
# ---------------------------------------------------------------------------


class TestChatWithRetry:
    """chat_with_retry() must call provider.chat_with_retry."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.provider = MagicMock()
        self.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="retry-ok"))
        set_llm(self.provider, "default-model")

    @pytest.mark.asyncio
    async def test_calls_chat_with_retry(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = await chat_with_retry(msgs)
        assert result.content == "retry-ok"
        self.provider.chat_with_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_messages(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        await chat_with_retry(msgs)
        _, kwargs = self.provider.chat_with_retry.call_args
        assert kwargs["messages"] == msgs

    @pytest.mark.asyncio
    async def test_auto_injects_model(self) -> None:
        await chat_with_retry([{"role": "user", "content": "hi"}])
        _, kwargs = self.provider.chat_with_retry.call_args
        assert kwargs["model"] == "default-model"

    @pytest.mark.asyncio
    async def test_model_override(self) -> None:
        await chat_with_retry([{"role": "user", "content": "hi"}], model="override")
        _, kwargs = self.provider.chat_with_retry.call_args
        assert kwargs["model"] == "override"

    @pytest.mark.asyncio
    async def test_passes_retry_mode(self) -> None:
        await chat_with_retry([{"role": "user", "content": "hi"}], retry_mode="aggressive")
        _, kwargs = self.provider.chat_with_retry.call_args
        assert kwargs["retry_mode"] == "aggressive"


# ---------------------------------------------------------------------------
# Error: set_llm never called
# ---------------------------------------------------------------------------


class TestWithoutSetLlm:
    """When set_llm() was never called, calling any function must raise LookupError.

    Note: uses a fresh ContextVar via monkeypatch because prior sync tests
    in this file may have set the module-level ContextVars in the main context.
    """

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import contextvars

        from nanobot.agent import llm_context

        monkeypatch.setattr(
            llm_context, "_llm_provider",
            contextvars.ContextVar("fresh_provider"),
        )
        monkeypatch.setattr(
            llm_context, "_llm_model",
            contextvars.ContextVar("fresh_model"),
        )

    @pytest.mark.asyncio
    async def test_chat_raises_lookuperror(self) -> None:
        with pytest.raises(LookupError):
            await chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_stream_with_retry_raises_lookuperror(self) -> None:
        with pytest.raises(LookupError):
            await chat_stream_with_retry([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_with_retry_raises_lookuperror(self) -> None:
        with pytest.raises(LookupError):
            await chat_with_retry([{"role": "user", "content": "hi"}])
