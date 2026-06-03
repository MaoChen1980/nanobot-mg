"""Context governance — message trimming, snipping."""
from __future__ import annotations

from typing import Any

from nanobot.session.manager import find_legal_message_start

from .runner_constants import (
    _BACKFILL_CONTENT,
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
    """Drop orphan tool results and strip duplicate/consumed tool_calls.

    1. Drop tool results whose ID was never declared by any assistant.
    2. Strip tool_calls from assistant messages when their ID was already
       consumed by a completed assistant→tool pair from an *earlier* turn.
    3. Strip tool_calls with duplicate IDs within the same assistant message.
    4. Drop tool results whose ID was already fulfilled (duplicate turn).

    This prevents ``_sanitize_messages`` from silently removing only the
    *result* (via ``_skip``) while the orphaned tool_call survives and
    triggers MiniMax error 2013.
    """
    fulfilled: set[str] = set()     # IDs that completed assistant→tool cycle
    all_declared: set[str] = set()  # all IDs ever declared across history
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")

        if role == "assistant":
            tcs = msg.get("tool_calls")
            if not tcs:
                result.append(msg)
                continue

            seen_in_turn: set[str] = set()
            new_tcs: list[dict[str, Any]] = []
            for tc in tcs:
                if not isinstance(tc, dict):
                    new_tcs.append(tc)
                    continue
                tid = tc.get("id")
                if not tid:
                    new_tcs.append(tc)
                    continue
                str_tid = str(tid)
                if str_tid in fulfilled or str_tid in seen_in_turn:
                    continue  # strip — already consumed or duplicate in this turn
                seen_in_turn.add(str_tid)
                new_tcs.append(tc)
                all_declared.add(str_tid)

            if len(new_tcs) == len(tcs):
                result.append(msg)
            else:
                d = dict(msg)
                if new_tcs:
                    d["tool_calls"] = new_tcs
                else:
                    d.pop("tool_calls", None)
                result.append(d)

        elif role == "tool":
            tid = msg.get("tool_call_id")
            if not tid:
                result.append(msg)
                continue
            str_tid = str(tid)
            if str_tid not in all_declared or str_tid in fulfilled:
                continue  # orphan tool result or duplicate turn
            fulfilled.add(str_tid)
            result.append(msg)

        else:
            result.append(msg)

    # Return original list when no changes were made
    if len(result) == len(messages) and all(a is b for a, b in zip(result, messages)):
        return messages
    return result


def strip_bypassed_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove tool messages with [BYPASSED] or [PENDING] content.

    These are transient state from interruption that should not be sent to the
    LLM — they represent tool calls that were cancelled or interrupted, and
    keeping them inflates history with irrelevant status messages.
    """
    kept: list[dict[str, Any]] = []
    changed = False
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and ("[BYPASSED]" in content or "[PENDING]" in content):
                changed = True
                continue
        kept.append(msg)
    # Normalize dicts only when stripping occurred to avoid unnecessary copies
    if not changed:
        return messages
    return [dict(m) for m in kept]


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


