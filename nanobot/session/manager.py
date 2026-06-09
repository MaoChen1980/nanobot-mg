"""Session management for conversation history."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.media_decode import image_placeholder_text


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _split_turns_by_assistant(messages: list[dict]) -> list[list[dict]]:
        """Split messages into turns, each turn starts with an assistant message."""
        turns: list[list[dict]] = []
        current: list[dict] = []
        for msg in messages:
            if msg.get("role") == "assistant":
                if current:
                    turns.append(current)
                current = [msg]
            else:
                current.append(msg)
        if current:
            turns.append(current)
        return turns

    @staticmethod
    def _format_timestamp(ts: str, timezone: str | None = None) -> str | None:
        """Convert ISO timestamp to human-readable string (or None if invalid)."""
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            if timezone:
                from zoneinfo import ZoneInfo
                dt = dt.astimezone(ZoneInfo(timezone))
            tz_abbr = dt.strftime("%Z") or (timezone or "UTC")
            return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} {tz_abbr}"
        except Exception:
            return None

    def add_message(self, role: str, content: str, timestamp: str | None = None, **kwargs: Any) -> None:
        """Add a message to the session. *timestamp* should be an ISO-format str if provided."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc)

    def format_history(
        self,
        *,
        include_timestamps: bool = False,
        timezone: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return formatted messages for LLM input (no truncation).

        Applies media breadcrumbs, timestamp formatting, and orphan tool
        result cleanup.  Does **not** truncate by turn/message/token count
        — compression is handled by the caller (see
        :func:`nanobot.agent.compress`).

        When *timezone* (e.g. ``"Asia/Shanghai"``) is given together with
        ``include_timestamps=True``, timestamps are converted to that timezone
        so they stay consistent with the runtime context's ``Current Time``.
        """
        unconsolidated = [m for m in self.messages if m.get("status") != "excluded"]

        # Avoid starting mid-turn when possible, except for proactive
        # assistant deliveries that the user may be replying to.
        sliced = list(unconsolidated)
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                if message.get("status") == "synthetic":
                    start = i - 1 if i > 0 else 0
                else:
                    start = i
                    if i > 0 and sliced[i - 1].get("_channel_delivery"):
                        start = i - 1
                sliced = sliced[start:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            content = message.get("content", "")
            # Synthesize an ``[image: path]`` breadcrumb from the persisted
            # ``media`` kwarg so LLM replay still sees *something* where the
            # image used to be. Without this, an image-only user turn
            # replays as an empty user message — the assistant's reply then
            # looks like it's responding to nothing.
            media = message.get("media")
            if isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "reasoning_details", "thinking_blocks", "status"):
                if key in message:
                    entry[key] = message[key]
            if include_timestamps:
                formatted_ts = self._format_timestamp(message.get("timestamp"), timezone=timezone)
                if formatted_ts:
                    entry["timestamp"] = formatted_ts
            out.append(entry)

        return out

    # Backward-compat alias
    def get_history(
        self,
        max_messages: int = 0,
        *,
        max_tokens: int = 0,
        max_turns: int = 0,
        include_timestamps: bool = False,
        timezone: str | None = None,
    ) -> list[dict[str, Any]]:
        """Deprecated: use :meth:`format_history` instead."""
        logger.warning(
            "Session.get_history() is deprecated, use format_history() instead "
            "(called with max_messages=%s, max_tokens=%s, max_turns=%s)",
            max_messages, max_tokens, max_turns,
        )
        return self.format_history(include_timestamps=include_timestamps, timezone=timezone)

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.updated_at = datetime.now(timezone.utc)


class SessionManager:
    """
    Manages conversation sessions backed by a database.

    Uses NanobotDB for persistence. Falls back to in-memory-only when
    no database is provided (useful for testing Session operations).
    """

    def __init__(self, db=None):
        self._db = db
        self._cache: dict[str, Session] = {}
        # threading.Lock is safe here: no await points in this file, so no
        # async deadlock risk. Per-session dispatch (asyncio.Lock in loop.py
        # _dispatch) also serializes async access per session. threading.Lock
        # additionally protects against HTTP gateway sync access from other threads.
        self._cache_lock = threading.Lock()

    def get_or_create(self, key: str) -> Session:
        """Get an existing session or create a new one."""
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        with self._cache_lock:
            self._cache[key] = session
        return session

    @staticmethod
    def _fix_tool_protocol_violations(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove tool_calls from assistant messages that lack corresponding tool results.

        Must run AFTER ``_strip_bypassed_tool_messages`` so BYPASSED/PENDING
        tool results are already gone.  Verifies every individual
        ``tool_call_id`` against all following tool results — the old approach
        of just checking the next-message role missed cases where a subset of
        tool_calls had their results stripped.
        """
        fulfilled: set[str] = set()
        for msg in messages:
            if msg.get("role") == "tool":
                tid = msg.get("tool_call_id")
                if isinstance(tid, str):
                    fulfilled.add(tid)

        fixed = 0
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tcs = msg["tool_calls"]
                keep = [tc for tc in tcs if isinstance(tc, dict) and tc.get("id") in fulfilled]
                if not keep:
                    msg.pop("tool_calls", None)
                    fixed += 1
                elif len(keep) < len(tcs):
                    msg["tool_calls"] = keep
                    fixed += 1
        if fixed:
            logger.info("Fixed {} orphaned tool_calls in session load", fixed)
        return messages

    @staticmethod
    def _strip_bypassed_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove tool messages that start with [BYPASSED].

        These are transient state from was_interrupted that should not be
        persisted or replayed — they cause duplicate tool_call_id errors.
        Handles both direct [BYPASSED] prefix and [Message Time: ...]\n[BYPASSED]
        format (the prefix is added by session manager annotation).
        """
        original_count = len(messages)
        filtered = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and ("[BYPASSED]" in content or "[PENDING]" in content):
                    continue
            filtered.append(msg)
        dropped = original_count - len(filtered)
        if dropped:
            logger.info("Dropped {} bypassed/pending tool messages from session", dropped)
        return filtered

    def _load(self, key: str) -> Session | None:
        """Load a session from the database."""
        if self._db is None:
            return None
        session = self._db.load_session(key)
        if session is None:
            return None
        session.messages = self._strip_bypassed_tool_messages(session.messages)
        session.messages = self._fix_tool_protocol_violations(session.messages)
        return session

    def save(self, session: Session) -> None:
        """Save a session to the database (full save: delete + re-insert)."""
        if self._db is not None:
            self._db.save_session(session)

        with self._cache_lock:
            self._cache[session.key] = session

    def flush_all(self) -> int:
        """Re-save every cached session.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        with self._cache_lock:
            items = list(self._cache.items())
        for key, session in items:
            try:
                self.save(session)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        with self._cache_lock:
            self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Remove a session from the database and in-memory cache."""
        self.invalidate(key)
        if self._db is not None:
            self._db.delete_session(key)
            return True
        return False

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from the database without caching."""
        session = self._load(key)
        if session is None:
            return None
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions from the database."""
        if self._db is not None:
            return self._db.list_sessions()
        return []


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Find the first index whose tool results have matching assistant calls.

    Never returns a value that would drop all messages — returning
    ``len(messages)`` means every tool is orphaned, which is a data-quality
    issue, not a reason to discard everything.
    """
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
                for prev in messages[start : i + 1]:
                    if prev.get("role") == "assistant":
                        for tc in prev.get("tool_calls") or []:
                            if isinstance(tc, dict) and tc.get("id"):
                                declared.add(str(tc["id"]))
    if start >= len(messages):
        start = 0
    return start
