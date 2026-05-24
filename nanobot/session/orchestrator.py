"""Session lifecycle orchestration for message handlers.

Consolidates the common prepare → finalize pattern so handlers
don't duplicate session get/create, checkpoint restore, and save logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.agent.loop_checkpoint import RecoveryManager
    from nanobot.bus.events import InboundMessage
    from nanobot.session.manager import Session, SessionManager


class SessionLifecycle:
    """Orchestrates session lifecycle: prepare → finalize.

    Wraps :class:`SessionManager` and :class:`RecoveryManager` into
    higher-level operations shared by all message handlers.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        recovery: RecoveryManager,
    ) -> None:
        self._sm = session_manager
        self._recovery = recovery

    # ── low-level pass-through ──────────────────────────────────────────

    def get_or_create(self, key: str) -> Session:
        return self._sm.get_or_create(key)

    def save(self, session: Session) -> None:
        self._sm.save(session)

    # ── prepare phase ───────────────────────────────────────────────────

    def prepare(self, key: str) -> Session:
        """Get-or-create session and restore any crash checkpoints."""
        session = self._sm.get_or_create(key)
        self._recovery.restore_and_clear_checkpoint(session)
        self._recovery.restore_pending_user_turn(session)
        return session

    # ── mid-turn persistence ─────────────────────────────────────────────

    def persist_user_message(
        self, session: Session, msg: InboundMessage, pending_ask_id: Any,
    ) -> bool:
        """Persist a user message early (before the agent loop runs).

        Returns ``True`` when the message was actually persisted.
        """
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if pending_ask_id or not (has_text or media_paths):
            return False
        extra: dict[str, Any] = {"media": list(media_paths)} if media_paths else {}
        session.add_message(
            "user", msg.content,
            timestamp=msg.timestamp.isoformat(),
            **extra,
        )
        self._recovery.mark_pending_user_turn(session)
        return True

    # ── finalize phase ──────────────────────────────────────────────────

    def finalize(self, session: Session) -> list[dict]:
        """Complete a turn: trim, enforce cap, clear checkpoints, save.

        Returns any trimmed messages for the caller to archive to history.
        """
        max_turns = session.metadata.get("max_turns", 200)
        trim_batch = session.metadata.get("trim_batch", 50)
        trimmed = session.trim_old_turns(max_turns, trim_batch)
        session.enforce_file_cap()
        self._recovery.clear_pending_user_turn(session)
        self._recovery.clear_runtime_checkpoint(session)
        self._sm.save(session)
        return trimmed

    def finalize_ephemeral(self, session: Session) -> None:
        """Lightweight finalize for ephemeral messages (no history)."""
        self._recovery.clear_runtime_checkpoint(session)
        self._sm.save(session)

    # ── error recovery ──────────────────────────────────────────────────

    def cleanup_on_error(self, key: str) -> bool:
        """Clean up pending-user-turn for a session after an error.

        Returns ``True`` if any cleanup was performed (and saved).
        """
        session = self._sm.get_or_create(key)
        cleared = self._recovery.clear_pending_user_turn(session)
        if cleared:
            self._sm.save(session)
        return cleared
