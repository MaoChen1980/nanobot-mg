"""LLM request/response handling for AgentRunner."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.context_vars import _current_debug_enabled
from .compressor import CompressEvent
from .message_pipe import MessagePipe

# Module-level pipe instance (stateless, handles overflow automatically)
_message_pipe = MessagePipe()


async def request_model(
    spec: Any,
    messages: list[dict[str, Any]],
    hook: Any,
    context: Any,
) -> tuple[Any, CompressEvent | None]:
    """Make an LLM request with optional streaming.

    Returns ``(response, compress_event)`` — 如果 MessagePipe 发生过
    overflow 压缩则返回 ``CompressEvent``（含压缩后消息列表和待持久化的
    原始消息）供 caller 同步回 ``messages``，否则 ``compress_event`` 为
    ``None``。
    """
    if _current_debug_enabled.get():
        _dump_messages_to_debug_dir(messages)
    timeout_s: float | None = spec.llm_timeout_s
    if timeout_s is None:
        raw = os.environ.get("NANOBOT_LLM_TIMEOUT_S", "900").strip()
        try:
            timeout_s = float(raw)
        except (TypeError, ValueError):
            timeout_s = 900.0
    if timeout_s is not None and timeout_s <= 0:
        timeout_s = None

    kwargs = _build_request_kwargs(spec, messages, tools=spec.tools.get_definitions())

    async def _stream(delta: str) -> None:
        await hook.on_stream(context, delta)
    async def _reasoning(delta: str) -> None:
        await hook.on_reasoning(context, delta)

    pipe_kwargs = {k: v for k, v in kwargs.items() if k != "messages"}
    coro = _message_pipe.complete_stream(
        messages=messages,
        budget=spec.history_token_limit,
        previous_summary=spec.previous_summary,
        on_content_delta=_stream,
        on_reasoning_delta=_reasoning,
        **pipe_kwargs,
    )

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
        ), None


def _build_request_kwargs(
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
    spec: Any,
    messages: list[dict[str, Any]],
    *,
    has_assessment: bool = False,
) -> Any:
    """Request finalization retry when model returns empty content.

    Returns only the response (compressed messages are discarded since
    finalization runs at the end of the agent loop).
    """
    from nanobot.utils.runtime import build_finalization_retry_message

    retry_messages = list(messages)
    retry_messages.append(build_finalization_retry_message(has_assessment=has_assessment))
    kwargs = _build_request_kwargs(spec, retry_messages, tools=None)
    pipe_kwargs = {k: v for k, v in kwargs.items() if k != "messages"}
    response, _ = await _message_pipe.complete(
        messages=retry_messages,
        budget=spec.history_token_limit,
        previous_summary=spec.previous_summary,
        **pipe_kwargs,
    )
    return response


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


def _dump_messages_to_debug_dir(messages: list[dict[str, Any]]) -> None:
    """Dump raw messages to ~/.nanobot/debug/ as a JSON file.

    Creates one file per call with a timestamp+microsecond filename so
    each prompt dump is unique and sortable.
    """
    debug_dir = Path.home() / ".nanobot" / "debug"
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Failed to create debug dir: {}", debug_dir)
        return

    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S_%f") + ".json"
    path = debug_dir / filename

    # Strip out binary/image content from media blocks for readability
    sanitized: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            cleaned: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    # Replace base64 image data with a placeholder
                    cleaned.append({**block, "image_url": {"url": "[base64 image data omitted]"}})
                else:
                    cleaned.append(block)
            sanitized.append({**msg, "content": cleaned})
        else:
            sanitized.append(msg)

    payload = {
        "_meta": {"saved_at": now.isoformat(), "message_count": len(sanitized)},
        "messages": sanitized,
    }

    try:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Prompt debug dump saved to {}", path)
    except OSError as e:
        logger.warning("Failed to write debug dump to {}: {}", path, e)