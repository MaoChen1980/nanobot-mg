"""Session checkpoint management for AgentLoop."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


def checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
    return (
        message.get("role"),
        message.get("content"),
        message.get("tool_call_id"),
        message.get("name"),
        message.get("tool_calls"),
        message.get("reasoning_content"),
        message.get("thinking_blocks"),
    )


def set_runtime_checkpoint(session: Any, payload: dict[str, Any]) -> None:
    """Persist the latest in-flight turn state into session metadata."""
    from nanobot.agent.loop_constants import _RUNTIME_CHECKPOINT_KEY
    session.metadata[_RUNTIME_CHECKPOINT_KEY] = payload


def clear_runtime_checkpoint(session: Any) -> None:
    """Remove the runtime checkpoint from session metadata."""
    from nanobot.agent.loop_constants import _RUNTIME_CHECKPOINT_KEY
    if _RUNTIME_CHECKPOINT_KEY in session.metadata:
        session.metadata.pop(_RUNTIME_CHECKPOINT_KEY, None)


def mark_pending_user_turn(session: Any) -> None:
    from nanobot.agent.loop_constants import _PENDING_USER_TURN_KEY
    session.metadata[_PENDING_USER_TURN_KEY] = True


def clear_pending_user_turn(session: Any) -> None:
    from nanobot.agent.loop_constants import _PENDING_USER_TURN_KEY
    session.metadata.pop(_PENDING_USER_TURN_KEY, None)


def restore_runtime_checkpoint(loop: Any, session: Any) -> bool:
    """Materialize an unfinished turn into session history before a new request."""
    from nanobot.agent.loop_constants import _RUNTIME_CHECKPOINT_KEY, _PENDING_USER_TURN_KEY

    checkpoint = session.metadata.get(_RUNTIME_CHECKPOINT_KEY)
    if not isinstance(checkpoint, dict):
        return False

    assistant_message = checkpoint.get("assistant_message")
    completed_tool_results = checkpoint.get("completed_tool_results") or []
    pending_tool_calls = checkpoint.get("pending_tool_calls") or []

    restored_messages: list[dict[str, Any]] = []
    if isinstance(assistant_message, dict):
        restored = dict(assistant_message)
        restored.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        restored_messages.append(restored)
    for message in completed_tool_results:
        if isinstance(message, dict):
            restored = dict(message)
            restored.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            restored_messages.append(restored)
    for tool_call in pending_tool_calls:
        if not isinstance(tool_call, dict):
            continue
        tool_id = tool_call.get("id")
        name = ((tool_call.get("function") or {}).get("name")) or "tool"
        restored_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_id,
                "name": name,
                "content": "Error: Task interrupted before this tool finished.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    overlap = 0
    max_overlap = min(len(session.messages), len(restored_messages))
    for size in range(max_overlap, 0, -1):
        existing = session.messages[-size:]
        restored = restored_messages[:size]
        if all(
            checkpoint_message_key(left) == checkpoint_message_key(right)
            for left, right in zip(existing, restored)
        ):
            overlap = size
            break
    session.messages.extend(restored_messages[overlap:])

    clear_pending_user_turn(session)
    clear_runtime_checkpoint(session)
    return True


def restore_pending_user_turn(session: Any) -> bool:
    """Close a turn that only persisted the user message before crashing."""
    from nanobot.agent.loop_constants import _PENDING_USER_TURN_KEY

    if not session.metadata.get(_PENDING_USER_TURN_KEY):
        return False

    if session.messages and session.messages[-1].get("role") == "user":
        session.messages.append(
            {
                "role": "assistant",
                "content": "Error: Task interrupted before a response was generated.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        session.updated_at = datetime.now()

    clear_pending_user_turn(session)
    return True