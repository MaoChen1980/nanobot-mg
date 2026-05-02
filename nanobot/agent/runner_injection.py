"""Injection handling — mid-turn message injection and drainage."""

from __future__ import annotations

import inspect
from typing import Any

from loguru import logger

from .runner_constants import _MAX_INJECTIONS_PER_TURN, _MAX_INJECTION_CYCLES


async def drain_injections(spec: Any) -> list[dict[str, Any]]:
    """Drain pending user messages via the injection callback.

    Returns normalized user messages (capped by _MAX_INJECTIONS_PER_TURN),
    or an empty list when there is nothing to inject.
    """
    if spec.injection_callback is None:
        return []
    try:
        signature = inspect.signature(spec.injection_callback)
        accepts_limit = (
            "limit" in signature.parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        )
        if accepts_limit:
            items = await spec.injection_callback(limit=_MAX_INJECTIONS_PER_TURN)
        else:
            items = await spec.injection_callback()
    except Exception:
        logger.exception("injection_callback failed")
        return []
    if not items:
        return []

    injected_messages: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("role") == "user" and "content" in item:
            injected_messages.append(item)
            continue
        text = getattr(item, "content", str(item))
        if text.strip():
            injected_messages.append({"role": "user", "content": text})

    if len(injected_messages) > _MAX_INJECTIONS_PER_TURN:
        dropped = len(injected_messages) - _MAX_INJECTIONS_PER_TURN
        logger.warning(
            "Injection callback returned {} messages, capping to {} ({} dropped)",
            len(injected_messages), _MAX_INJECTIONS_PER_TURN, dropped,
        )
        injected_messages = injected_messages[:_MAX_INJECTIONS_PER_TURN]

    return injected_messages


def build_tool_call_status_messages(
    messages: list[dict[str, Any]],
    has_new_injections: bool = False,
) -> list[dict[str, Any]] | None:
    """Build standardized tool result messages for injection context.

    Returns tool messages for pending/in-progress tool calls:
    - Completed: excluded (already have tool results in messages)
    - Abandoned: [ABANDONED] status (when has_new_injections=True)
    - Pending: [PENDING] status (when has_new_injections=False)
    """
    if not messages:
        return None

    last_assistant = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            last_assistant = msg
            break
    if not last_assistant:
        return None

    completed_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tool_id = msg.get("tool_call_id")
            if tool_id:
                completed_ids.add(str(tool_id))

    tool_messages = []
    for tc in last_assistant.get("tool_calls", []):
        tid = tc.get("id")
        if not tid or str(tid) in completed_ids:
            continue
        name = tc.get("function", {}).get("name", "unknown")
        content = (
            f"[ABANDONED] Tool '{name}' (id: {tid}) was interrupted by new user instruction."
            if has_new_injections else
            f"[PENDING] Tool '{name}' (id: {tid}) is still in progress, waiting for result."
        )
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tid,
            "name": name,
            "content": content,
        })

    return tool_messages if tool_messages else None


def append_injected_messages(
    messages: list[dict[str, Any]],
    injections: list[dict[str, Any]],
    assistant_message: dict[str, Any] | None = None,
) -> None:
    """Append injected user messages while preserving role alternation.

    Also injects standardized tool messages for pending/abandoned tool calls.
    Tool status messages are only generated when we have an active
    assistant_message (i.e. during an active tool-call round).
    """
    from .runner_context import merge_message_content

    has_new_injections = bool(injections)
    tool_status_messages = None
    if assistant_message is not None:
        tool_status_messages = build_tool_call_status_messages(messages, has_new_injections)

    if tool_status_messages:
        for tm in tool_status_messages:
            messages.append(tm)

    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    for injection in injections:
        if (
            last_user_idx is not None
            and injection.get("role") == "user"
            and messages[last_user_idx].get("role") == "user"
        ):
            merged = dict(messages[last_user_idx])
            merged["content"] = merge_message_content(
                merged.get("content"),
                injection.get("content"),
            )
            messages[last_user_idx] = merged
            continue
        messages.append(injection)
        if injection.get("role") == "user":
            last_user_idx = len(messages) - 1