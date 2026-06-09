"""Session checkpoint and crash-recovery management for AgentLoop."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.session.manager import Session

from nanobot.agent.loop_constants import _RUNTIME_CHECKPOINT_KEY, _PENDING_USER_TURN_KEY


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
    """Store the latest in-flight turn state into session metadata."""
    session.metadata[_RUNTIME_CHECKPOINT_KEY] = payload


def clear_runtime_checkpoint(session: Any) -> None:
    """Remove the runtime checkpoint from session metadata."""
    if _RUNTIME_CHECKPOINT_KEY in session.metadata:
        session.metadata.pop(_RUNTIME_CHECKPOINT_KEY, None)


def mark_pending_user_turn(session: Any) -> None:
    session.metadata[_PENDING_USER_TURN_KEY] = True


def clear_pending_user_turn(session: Any) -> None:
    session.metadata.pop(_PENDING_USER_TURN_KEY, None)


def restore_and_clear_checkpoint(
    loop: Any, session: Any, *,
    pending_tool_content: str | None = None,
) -> bool:
    """Materialize an unfinished turn into session history before a new request.

    *pending_tool_content* — custom content for interrupted tool messages
    (used by /stop to write ``[STOPPED BY USER]`` instead of error text).
    """
    checkpoint = session.metadata.get(_RUNTIME_CHECKPOINT_KEY)
    if not isinstance(checkpoint, dict):
        return False

    assistant_message = checkpoint.get("assistant_message")
    completed_tool_results = checkpoint.get("completed_tool_results") or []
    pending_tool_calls = checkpoint.get("pending_tool_calls") or []

    default_pending = "Error: Task interrupted before this tool finished."

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
                "content": pending_tool_content or default_pending,
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
        session.updated_at = datetime.now(timezone.utc)

    clear_pending_user_turn(session)
    return True


class RecoveryManager:
    """Manages session checkpoint/restore and pending-turn recovery.

    Operates on session metadata only — never calls ``sessions.save()``.
    Callers are responsible for persisting after recovery operations if needed.
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    def set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Store the latest in-flight turn state into session metadata."""
        set_runtime_checkpoint(session, payload)

    def clear_runtime_checkpoint(self, session: Session) -> None:
        """Remove the runtime checkpoint from session metadata."""
        clear_runtime_checkpoint(session)

    def mark_pending_user_turn(self, session: Session) -> None:
        """Mark that a user message has been persisted mid-turn."""
        mark_pending_user_turn(session)

    def clear_pending_user_turn(self, session: Session) -> None:
        """Clear the pending-user-turn flag from session metadata."""
        clear_pending_user_turn(session)

    def restore_and_clear_checkpoint(self, session: Session, *, pending_tool_content: str | None = None) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        return restore_and_clear_checkpoint(self._loop, session, pending_tool_content=pending_tool_content)

    def restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        return restore_pending_user_turn(session)
