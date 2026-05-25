"""Context governance — message trimming, snipping."""
from __future__ import annotations

from typing import Any

from nanobot.utils.helpers import estimate_message_tokens, estimate_prompt_tokens_chain, format_message_header
from nanobot.session.manager import find_legal_message_start

from .runner_constants import (
    _BACKFILL_CONTENT,
    _SNIP_SAFETY_BUFFER,
)


def merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
    """Merge two message contents (used by both runner and context builder)."""
    if isinstance(left, str) and isinstance(right, str):
        return f"{left}\n\n{right}" if left else right

    def _to_blocks(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
        if value is None:
            return []
        return [{"type": "text", "text": str(value)}]

    return _to_blocks(left) + _to_blocks(right)


def drop_orphan_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool results that have no matching assistant tool_call earlier in the history."""
    declared: set[str] = set()
    updated: list[dict[str, Any]] | None = None
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        if role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                if updated is None:
                    updated = [dict(m) for m in messages[:idx]]
                continue
        if updated is not None:
            updated.append(dict(msg))
    return updated if updated is not None else messages


def backfill_missing_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert synthetic error results for orphaned tool_use blocks."""
    declared: list[tuple[int, str, str]] = []
    fulfilled: set[str] = set()
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    name = ""
                    func = tc.get("function")
                    if isinstance(func, dict):
                        name = func.get("name", "")
                    declared.append((idx, str(tc["id"]), name))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                fulfilled.add(str(tid))

    missing = [(ai, cid, name) for ai, cid, name in declared if cid not in fulfilled]
    if not missing:
        return messages

    updated = list(messages)
    offset = 0
    for assistant_idx, call_id, name in missing:
        insert_at = assistant_idx + 1 + offset
        while insert_at < len(updated) and updated[insert_at].get("role") == "tool":
            insert_at += 1
        updated.insert(insert_at, {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": _BACKFILL_CONTENT,
        })
        offset += 1
    return updated


def trim_history_to_budget(
    provider: Any,
    spec: Any,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Truncate history to fit within token budget — only when necessary."""
    if not messages or not spec.context_window_tokens:
        return messages

    provider_max_tokens = getattr(getattr(provider, "generation", None), "max_tokens", 4096)
    max_output = spec.max_tokens if isinstance(spec.max_tokens, int) else (
        provider_max_tokens if isinstance(provider_max_tokens, int) else 4096
    )
    # Cap output reservation so history isn't starved.  The provider's
    # max_tokens (e.g. 160K) is the *maximum* the API allows, not what
    # we must reserve — capping at 16K matches the handler-level budget
    # in _compute_history_budget so that trim doesn't re-drop history.
    max_output = min(max_output, 16384)
    # Dynamic budget: what the context window allows after output + safety.
    # context_block_limit can only raise this floor (never shrink it), since
    # history has already been limited at the message-handler level.
    budget = spec.context_window_tokens - max_output - _SNIP_SAFETY_BUFFER
    if spec.context_block_limit:
        budget = max(spec.context_block_limit, budget)
    if budget <= 0:
        return messages

    estimate, _ = estimate_prompt_tokens_chain(
        provider,
        spec.model,
        messages,
        spec.tools.get_definitions(),
    )
    if estimate <= budget:
        return messages

    system_messages = [dict(msg) for msg in messages if msg.get("role") == "system"]
    non_system = [dict(msg) for msg in messages if msg.get("role") != "system"]
    if not non_system:
        return messages

    system_tokens = sum(estimate_message_tokens(msg) for msg in system_messages)
    remaining_budget = max(128, budget - system_tokens)
    kept: list[dict[str, Any]] = []
    kept_tokens = 0
    for message in reversed(non_system):
        msg_tokens = estimate_message_tokens(message)
        if kept and kept_tokens + msg_tokens > remaining_budget:
            break
        kept.append(message)
        kept_tokens += msg_tokens
    kept.reverse()

    # Remove synthetic user "ok" orphaned when its companion summary was trimmed.
    while kept and kept[0].get("status") == "synthetic" and kept[0].get("role") == "user":
        kept.pop(0)

    if kept:
        for i, message in enumerate(kept):
            if message.get("role") == "user" and message.get("status") != "synthetic":
                kept = kept[i:]
                break
        else:
            for idx in range(len(non_system) - 1, -1, -1):
                if non_system[idx].get("role") == "user" and non_system[idx].get("status") != "synthetic":
                    kept = non_system[idx:]
                    break
            else:
                kept = non_system[-min(len(non_system), 4):]
        start = find_legal_message_start(kept)
        if start:
            kept = kept[start:]
    if not kept:
        kept = non_system[-min(len(non_system), 4):]
        start = find_legal_message_start(kept)
        if start:
            kept = kept[start:]
    return system_messages + kept
