"""LLM request/response handling for AgentRunner."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger

from .runner_constants import _DEFAULT_ERROR_MESSAGE


async def request_model(
    provider: Any,
    spec: Any,
    messages: list[dict[str, Any]],
    hook: Any,
    context: Any,
) -> Any:
    """Make an LLM request with optional streaming."""
    timeout_s: float | None = spec.llm_timeout_s
    if timeout_s is None:
        raw = os.environ.get("NANOBOT_LLM_TIMEOUT_S", "300").strip()
        try:
            timeout_s = float(raw)
        except (TypeError, ValueError):
            timeout_s = 300.0
    if timeout_s is not None and timeout_s <= 0:
        timeout_s = None

    kwargs = _build_request_kwargs(provider, spec, messages, tools=spec.tools.get_definitions())

    if hook.wants_streaming():
        async def _stream(delta: str) -> None:
            await hook.on_stream(context, delta)
        coro = provider.chat_stream_with_retry(
            **kwargs,
            on_content_delta=_stream,
        )
    else:
        coro = provider.chat_with_retry(**kwargs)

    if timeout_s is None:
        return await coro
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        from nanobot.providers.base import LLMResponse
        return LLMResponse(
            content=f"Error calling LLM: timed out after {timeout_s:g}s",
            finish_reason="error",
            error_kind="timeout",
        )


def _build_request_kwargs(
    provider: Any,
    spec: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "model": spec.model,
        "retry_mode": spec.provider_retry_mode,
        "on_retry_wait": spec.retry_wait_callback,
    }
    if spec.temperature is not None:
        kwargs["temperature"] = spec.temperature
    if spec.max_tokens is not None:
        kwargs["max_tokens"] = spec.max_tokens
    if spec.reasoning_effort is not None:
        kwargs["reasoning_effort"] = spec.reasoning_effort
    return kwargs


async def request_finalization_retry(
    provider: Any,
    spec: Any,
    messages: list[dict[str, Any]],
) -> Any:
    """Request finalization retry when model returns empty content."""
    from nanobot.utils.runtime import build_finalization_retry_message

    retry_messages = list(messages)
    retry_messages.append(build_finalization_retry_message())
    kwargs = _build_request_kwargs(provider, spec, retry_messages, tools=None)
    return await provider.chat_with_retry(**kwargs)


def usage_dict(usage: dict[str, Any] | None) -> dict[str, int]:
    """Convert usage dict to normalized int values."""
    if not usage:
        return {}
    result: dict[str, int] = {}
    for key, value in usage.items():
        try:
            result[key] = int(value or 0)
        except (TypeError, ValueError):
            continue
    return result


def accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
    """Accumulate token usage counts."""
    for key, value in addition.items():
        target[key] = target.get(key, 0) + value


def merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    """Merge two usage dicts."""
    merged = dict(left)
    for key, value in right.items():
        merged[key] = merged.get(key, 0) + value
    return merged