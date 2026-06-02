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


