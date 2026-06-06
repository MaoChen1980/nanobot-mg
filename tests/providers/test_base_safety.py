"""Tests for LLMProvider safety/error-wrapping methods.

Covers: _safe_chat, _safe_chat_stream, _sleep_with_heartbeat.
These require async mocking.
"""

from __future__ import annotations

import asyncio

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse


class _TestProvider(LLMProvider):
    """Minimal provider subclass for testing base class async methods."""

    def __init__(self, chat_result=None, chat_stream_result=None):
        super().__init__()
        self._chat_result = chat_result
        self._chat_stream_result = chat_stream_result

    async def chat(self, **kwargs):
        if isinstance(self._chat_result, BaseException):
            raise self._chat_result
        return self._chat_result

    async def chat_stream(self, **kwargs):
        if isinstance(self._chat_stream_result, BaseException):
            raise self._chat_stream_result
        return self._chat_stream_result

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_safe_chat_passthrough():
    expected = LLMResponse(content="ok")
    provider = _TestProvider(chat_result=expected)
    result = await provider._safe_chat()
    assert result is expected


@pytest.mark.asyncio
async def test_safe_chat_wraps_exception():
    provider = _TestProvider(chat_result=RuntimeError("connection failed"))
    result = await provider._safe_chat()
    assert result.finish_reason == "error"
    assert "Error calling LLM" in (result.content or "")


@pytest.mark.asyncio
async def test_safe_chat_propagates_cancelled_error():
    provider = _TestProvider(chat_result=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await provider._safe_chat()


@pytest.mark.asyncio
async def test_safe_chat_stream_passthrough():
    expected = LLMResponse(content="streamed")
    provider = _TestProvider(chat_stream_result=expected)
    result = await provider._safe_chat_stream()
    assert result is expected


@pytest.mark.asyncio
async def test_safe_chat_stream_wraps_exception():
    provider = _TestProvider(chat_stream_result=RuntimeError("timeout"))
    result = await provider._safe_chat_stream()
    assert result.finish_reason == "error"
    assert "Error calling LLM" in (result.content or "")


@pytest.mark.asyncio
async def test_safe_chat_stream_propagates_cancelled():
    provider = _TestProvider(chat_stream_result=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await provider._safe_chat_stream()


@pytest.mark.asyncio
async def test_sleep_with_heartbeat_chunks(monkeypatch):
    delays = []

    async def _fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    provider = _TestProvider(chat_result=LLMResponse(content="ok"))
    provider._RETRY_HEARTBEAT_CHUNK = 30

    cb_calls = []

    async def _cb(msg):
        cb_calls.append(msg)

    await provider._sleep_with_heartbeat(65, attempt=2, persistent=False, on_retry_wait=_cb)

    assert len(delays) >= 2
    assert delays[0] == 30
    assert sum(delays) == pytest.approx(65, abs=1)
    assert len(cb_calls) == 3
    assert "retry" in cb_calls[0].lower()
    assert "attempt 2" in cb_calls[0]
