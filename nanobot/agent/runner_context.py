"""Context governance — message trimming, snipping, microcompact."""

from __future__ import annotations

from typing import Any

from nanobot.utils.helpers import estimate_message_tokens, find_legal_message_start, estimate_prompt_tokens_chain

from .runner_constants import (
    _BACKFILL_CONTENT,
    _COMPACTABLE_TOOLS,
    _MICROCOMPACT_KEEP_RECENT,
    _MICROCOMPACT_MIN_CHARS,
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


def microcompact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace old compactable tool results with one-line summaries."""
    compactable_indices: list[int] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "tool" and msg.get("name") in _COMPACTABLE_TOOLS:
            compactable_indices.append(idx)

    if len(compactable_indices) <= _MICROCOMPACT_KEEP_RECENT:
        return messages

    stale = compactable_indices[: len(compactable_indices) - _MICROCOMPACT_KEEP_RECENT]
    updated: list[dict[str, Any]] | None = None
    for idx in stale:
        msg = messages[idx]
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < _MICROCOMPACT_MIN_CHARS:
            continue
        name = msg.get("name", "tool")
        summary = f"[{name} result omitted from context]"
        if updated is None:
            updated = [dict(m) for m in messages]
        updated[idx]["content"] = summary

    return updated if updated is not None else messages


def apply_tool_result_budget(
    spec: Any,
    messages: list[dict[str, Any]],
    normalize_fn,
) -> list[dict[str, Any]]:
    """Apply max_tool_result_chars budget to tool messages."""
    updated = messages
    for idx, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        normalized = normalize_fn(
            spec,
            str(message.get("tool_call_id") or f"tool_{idx}"),
            str(message.get("name") or "tool"),
            message.get("content"),
        )
        if normalized != message.get("content"):
            if updated is messages:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = normalized
    return updated


def apply_microcompact_and_budget(
    spec: Any,
    messages: list[dict[str, Any]],
    normalize_fn,
) -> list[dict[str, Any]]:
    """Fused microcompact + apply_tool_result_budget in a single O(n) pass.

    This avoids allocating two separate intermediate lists when both
    transformations apply — we build at most one new list.
    """
    # Phase 1: collect stale compactable indices (same logic as microcompact)
    compactable_indices: list[int] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "tool" and msg.get("name") in _COMPACTABLE_TOOLS:
            compactable_indices.append(idx)

    has_stale = len(compactable_indices) > _MICROCOMPACT_KEEP_RECENT
    stale_indices: set[int] = set()
    if has_stale:
        stale_indices = set(compactable_indices[: len(compactable_indices) - _MICROCOMPACT_KEEP_RECENT])

    # Phase 2: single pass applying microcompact summaries + budget truncation
    updated: list[dict[str, Any]] | None = None
    for idx, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue

        msg_content = msg.get("content")

        # Microcompact: summarize stale entries with enough content
        if idx in stale_indices and isinstance(msg_content, str) and len(msg_content) >= _MICROCOMPACT_MIN_CHARS:
            name = msg.get("name", "tool")
            summary = f"[{name} result omitted from context]"
            content_to_use = summary
        else:
            content_to_use = msg_content

        # Budget: truncate content if needed
        normalized = normalize_fn(
            spec,
            str(msg.get("tool_call_id") or f"tool_{idx}"),
            str(msg.get("name") or "tool"),
            content_to_use,
        )

        # Only allocate if something changed
        if normalized != msg_content:
            if updated is None:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = normalized

    return updated if updated is not None else messages


def snip_history(
    provider: Any,
    spec: Any,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Truncate history to fit within token budget."""
    if not messages or not spec.context_window_tokens:
        return messages

    provider_max_tokens = getattr(getattr(provider, "generation", None), "max_tokens", 4096)
    max_output = spec.max_tokens if isinstance(spec.max_tokens, int) else (
        provider_max_tokens if isinstance(provider_max_tokens, int) else 4096
    )
    budget = spec.context_block_limit or (
        spec.context_window_tokens - max_output - _SNIP_SAFETY_BUFFER
    )
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

    if kept:
        for i, message in enumerate(kept):
            if message.get("role") == "user":
                kept = kept[i:]
                break
        else:
            for idx in range(len(non_system) - 1, -1, -1):
                if non_system[idx].get("role") == "user":
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