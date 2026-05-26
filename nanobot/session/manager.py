"""Session management for conversation history."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    safe_filename,
)
from nanobot.utils.media_decode import image_placeholder_text

HISTORY_MAX_MESSAGES = 120
FILE_MAX_MESSAGES = 2000


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

    def get_history(
        self,
        max_messages: int = HISTORY_MAX_MESSAGES,
        *,
        max_tokens: int = 0,
        max_turns: int = 0,
        include_timestamps: bool = False,
        timezone: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input.

        History is sliced by turn count (``max_turns``, each turn starts with an
        assistant message) or message count (``max_messages``), then by token
        budget from the tail (``max_tokens``) when provided.

        When *timezone* (e.g. ``"Asia/Shanghai"``) is given together with
        ``include_timestamps=True``, the ``[Message Time]`` annotations are
        converted to that timezone so they stay consistent with the runtime
        context's ``Current Time``.
        """
        unconsolidated = [m for m in self.messages if m.get("status") != "excluded"]
        if max_turns > 0:
            turns = self._split_turns_by_assistant(unconsolidated)
            # Keep synthetic turns (summary pairs) regardless of max_turns limit
            synthetic = [t for t in turns if any(m.get("status") == "synthetic" for m in t)]
            regular = [t for t in turns if not any(m.get("status") == "synthetic" for m in t)]
            keep_regular = regular[-max(max_turns - len(synthetic), 0):] if len(regular) > max_turns else regular
            # Restore original order: synthetic first, then most recent regular turns
            keep = synthetic + keep_regular
            sliced = [m for turn in keep for m in turn]
        else:
            sliced = unconsolidated[-max_messages:]

        # Avoid starting mid-turn when possible, except for proactive
        # assistant deliveries that the user may be replying to.
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

        if max_tokens > 0 and out:
            # Slice by whole turns (assistant-anchored) instead of by message,
            # so the LLM never sees a partial turn with orphan tool calls.
            turns = self._split_turns_by_assistant(out)
            kept_turns: list[list[dict]] = []
            used = 0
            for turn in reversed(turns):
                turn_tokens = sum(estimate_message_tokens(m) for m in turn)
                if kept_turns and used + turn_tokens > max_tokens:
                    break
                kept_turns.append(turn)
                used += turn_tokens
            kept_turns.reverse()
            kept = [m for turn in kept_turns for m in turn]

            # Keep history aligned to the first visible user turn.
            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                # Tight token budgets can otherwise leave assistant-only tails.
                # If a user turn exists in the unsliced output, recover the
                # nearest one even if it slightly exceeds the token budget.
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            # And keep a legal tool-call boundary at the front.
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
            out = kept
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.updated_at = datetime.now(timezone.utc)

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix constrained by a hard message cap."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        retained = list(self.messages[-max_messages:])

        # Prefer starting at a user turn when one exists within the tail.
        first_user = next((i for i, m in enumerate(retained) if m.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]
        else:
            # If the tail is assistant/tool-only, anchor to the latest user in
            # the full session and take a capped forward window from there.
            latest_user = next(
                (i for i in range(len(self.messages) - 1, -1, -1)
                 if self.messages[i].get("role") == "user"),
                None,
            )
            if latest_user is not None:
                retained = list(self.messages[latest_user: latest_user + max_messages])

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        self.messages = retained
        self.updated_at = datetime.now(timezone.utc)

    def enforce_file_cap(
        self,
        on_archive: Any = None,
        limit: int = FILE_MAX_MESSAGES,
    ) -> None:
        """Bound session message growth by archiving and trimming old prefixes."""
        if limit <= 0 or len(self.messages) <= limit:
            return

        before_count = len(self.messages)
        self.retain_recent_legal_suffix(limit)
        dropped_count = before_count - len(self.messages)
        if dropped_count <= 0:
            return

        logger.info(
            "Session file cap hit for {}: dropped {}, kept {}",
            self.key,
            dropped_count,
            len(self.messages),
        )

    def trim_old_turns(self, max_turns: int = 200, trim_batch: int = 50) -> list[dict]:
        """When total turn count exceeds *max_turns*, remove oldest *trim_batch* turns.

        Returns the removed messages (flat) for the caller to archive to history.
        Each turn starts with an assistant message.
        """
        turns = self._split_turns_by_assistant(self.messages)

        if len(turns) < max_turns:
            return []

        trim_count = min(trim_batch, len(turns) - 1)  # keep at least 1 turn
        trim_turns = turns[:trim_count]
        keep = turns[trim_count:]

        trim_msg_count = sum(len(t) for t in trim_turns)
        keep_msg_count = sum(len(t) for t in keep)

        self.messages = [m for turn in keep for m in turn]
        self.updated_at = datetime.now(timezone.utc)

        logger.info(
            "Session {} turn-trim: archiving {} turns ({} msgs), keeping {} turns ({} msgs)",
            self.key, trim_count, trim_msg_count,
            len(keep), keep_msg_count,
        )

        return [m for turn in trim_turns for m in turn]


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, db=None):
        self.workspace = workspace
        self._db = db
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache: dict[str, Session] = {}
        # threading.Lock is safe here: no await points in this file, so no
        # async deadlock risk. Per-session dispatch (asyncio.Lock in loop.py
        # _dispatch) also serializes async access per session. threading.Lock
        # additionally protects against HTTP gateway sync access from other threads.
        self._cache_lock = threading.Lock()

    @staticmethod
    def safe_key(key: str) -> str:
        """Public helper used by HTTP handlers to map an arbitrary key to a stable filename stem."""
        return safe_filename(key.replace(":", "_"))

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
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

        When an assistant message with tool_calls is loaded from session but has no
        tool result immediately following it, the tool_calls must be cleared — otherwise
        the API returns "insufficient tool messages" error.

        Tool messages are no longer persisted (skip_tool_messages=True), so any
        assistant with tool_calls loaded from a saved session will be orphaned.
        """
        fixed = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                next_msg = messages[i + 1] if i + 1 < len(messages) else None
                if not (next_msg and next_msg.get("role") == "tool"):
                    msg.pop("tool_calls", None)
                    fixed += 1
        if fixed:
            logger.info("Fixed {} orphaned tool_calls in session load", fixed)
        return messages

    @staticmethod
    def _strip_abandoned_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove tool messages that start with [ABANDONED].

        These are transient state from was_interrupted that should not be
        persisted or replayed — they cause duplicate tool_call_id errors.
        Handles both direct [ABANDONED] prefix and [Message Time: ...]\n[ABANDONED]
        format (the prefix is added by session manager annotation).
        """
        original_count = len(messages)
        filtered = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and ("[ABANDONED]" in content or "[PENDING]" in content):
                    continue
            filtered.append(msg)
        dropped = original_count - len(filtered)
        if dropped:
            logger.info("Dropped {} abandoned/pending tool messages from session", dropped)
        return filtered

    def _load(self, key: str) -> Session | None:
        """Load a session from disk or DB."""
        if self._db is not None:
            return self._db.load_session(key)
        return self._load_from_file(key)

    def _load_from_file(self, key: str) -> Session | None:
        """Load a session from the JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                    else:
                        messages.append(data)

            messages = self._fix_tool_protocol_violations(messages)
            messages = self._strip_abandoned_tool_messages(messages)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(timezone.utc),
                updated_at=updated_at or datetime.now(timezone.utc),
                metadata=metadata,
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str) -> Session | None:
        """Attempt to recover a session from a corrupt JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            try:
                                created_at = datetime.fromisoformat(data["created_at"])
                            except (ValueError, TypeError):
                                pass
                        if data.get("updated_at"):
                            try:
                                updated_at = datetime.fromisoformat(data["updated_at"])
                            except (ValueError, TypeError):
                                pass
                    else:
                        messages.append(data)

            messages = self._fix_tool_protocol_violations(messages)
            messages = self._strip_abandoned_tool_messages(messages)

            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)

            if not messages and not metadata:
                return None

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(timezone.utc),
                updated_at=updated_at or datetime.now(timezone.utc),
                metadata=metadata,
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk atomically.

        When a DB instance is injected via *db*, delegates to
        :meth:`NanobotDB.save_session`. Otherwise falls back to the
        file-based JSONL writer.
        """
        if self._db is not None:
            self._db.save_session(session)
            with self._cache_lock:
                self._cache[session.key] = session
            return
        self._save_to_file(session, fsync=fsync)
        with self._cache_lock:
            self._cache[session.key] = session

    def _save_to_file(self, session: Session, *, fsync: bool = False) -> None:
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    # Only skip transient/abandoned tool messages — normal tool
                    # results must be persisted so assistant tool_calls stay valid.
                    if msg.get("role") == "tool":
                        content = msg.get("content", "")
                        if isinstance(content, str) and ("[ABANDONED]" in content or "[PENDING]" in content):
                            continue
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

            os.replace(tmp_path, path)

            if fsync:
                # fsync the directory so the rename is durable.
                # On Windows, opening a directory with O_RDONLY raises
                # PermissionError — skip the dir sync there (NTFS
                # journals metadata synchronously).
                try:
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except PermissionError:
                    pass  # Windows — directory fsync not supported
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def flush_all(self) -> int:
        """Re-save every cached session with fsync for durable shutdown.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        with self._cache_lock:
            items = list(self._cache.items())
        for key, session in items:
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        with self._cache_lock:
            self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Remove a session from disk/DB and the in-memory cache."""
        self.invalidate(key)  # invalidate acquires _cache_lock internally
        if self._db is not None:
            self._db.delete_session(key)
            return True
        return self._delete_session_file(key)

    def _delete_session_file(self, key: str) -> bool:
        """Delete session JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete session file {}: {}", path, e)
            return False

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from disk/DB without caching; intended for read-only HTTP endpoints."""
        session = self._load(key)
        if session is None:
            return None
        return self._session_payload(session)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions."""
        if self._db is not None:
            return self._db.list_sessions()
        return self._list_sessions_from_file()

    def _list_sessions_from_file(self) -> list[dict[str, Any]]:
        """List sessions from JSONL files."""
        sessions = []
        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem.replace("_", ":", 1)
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                logger.warning("Failed to parse session file, attempting repair: {}", path)
                repaired = self._repair(fallback_key)
                if repaired is not None:
                    sessions.append({
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": repaired.updated_at.isoformat(),
                        "path": str(path)
                    })
                continue
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)


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
