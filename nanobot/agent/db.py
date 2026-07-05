"""SQLite persistence layer for nanobot runtime data."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with timezone offset."""
    return datetime.now(timezone.utc).isoformat()


class NanobotDB:
    """SQLite-backed store for history, metadata, sessions, and messages."""

    def __init__(self, db_path: Path | str, *, workspace: Path | str | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
            self._lock = threading.Lock()
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.DatabaseError:
            logger.exception("Failed to open database at {}", self.db_path)
            raise
        self._workspace = Path(workspace) if workspace else Path.home() / ".nanobot" / "workspace"
        self._last_purge: datetime | None = None  # Timer-based purge to avoid O(N²) on every insert
        try:
            self._init_tables()
        except sqlite3.DatabaseError:
            logger.exception(
                "Database corruption detected at {}. "
                "Delete the file and restart to recreate it automatically.",
                self.db_path,
            )
            raise

    @contextmanager
    def _conn_access(self):
        """Context manager that serializes all access to sqlite3.Connection."""
        with self._lock:
            yield self._conn

    # --------------------------------------------------------------------------
    # Schema init
    # --------------------------------------------------------------------------

    def _init_tables(self) -> None:
        with self._conn_access() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    cursor INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    source TEXT,
                    project TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    confidence REAL DEFAULT 1.0
                );
                CREATE INDEX IF NOT EXISTS idx_facts_tags ON facts(tags);
                CREATE INDEX IF NOT EXISTS idx_facts_project ON facts(project);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_unique ON facts(fact);
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    turn INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    params TEXT,
                    result TEXT,
                    result_size INTEGER,
                    success INTEGER DEFAULT 1,
                    error TEXT,
                    duration_ms INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_key);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_name);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_time ON tool_calls(timestamp);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    summary TEXT DEFAULT '',
                    last_consolidated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    extra TEXT,
                    FOREIGN KEY (session_key) REFERENCES sessions(key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_key);
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
            """)
            conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply backward-compatible schema migrations for existing databases."""
        with self._conn_access() as conn:
            # Add summary column to sessions table (v1 -> v2)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.commit()

    @property
    def workspace(self) -> Path:
        return self._workspace

    # --------------------------------------------------------------------------
    # Metadata
    # --------------------------------------------------------------------------

    def get_metadata(self, key: str) -> str | None:
        with self._conn_access() as conn:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._conn_access() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    # --------------------------------------------------------------------------
    # History
    # --------------------------------------------------------------------------

    def append_history(self, content: str, *, timestamp: str | None = None, summary: str = "") -> int:
        ts = timestamp or _utc_now_iso()
        with self._conn_access() as conn:
            row = conn.execute("SELECT MAX(cursor) FROM history").fetchone()
            cursor = (row[0] or 0) + 1
            conn.execute(
                "INSERT INTO history (cursor, timestamp, content, summary) VALUES (?, ?, ?, ?)",
                (cursor, ts, re.sub(r"[\ud800-\udfff]", "�", content), summary),
            )
            conn.commit()
        self.set_metadata("cursor", str(cursor))
        return cursor

    def get_cursor(self) -> int:
        val = self.get_metadata("cursor")
        return int(val) if val else 0

    def set_cursor(self, cursor: int) -> None:
        self.set_metadata("cursor", str(cursor))

    def get_extractor_cursor(self) -> int:
        val = self.get_metadata("extractor_cursor")
        return int(val) if val else 0

    def set_extractor_cursor(self, cursor: int) -> None:
        self.set_metadata("extractor_cursor", str(cursor))

    @staticmethod
    def _row_to_dict(row: tuple, cols: list[str]) -> dict[str, Any]:
        """Convert a SQLite row tuple to a dict by column names."""
        return dict(zip(cols, row))

    def read_entries(self) -> list[dict[str, Any]]:
        cols = ["cursor", "timestamp", "content", "summary"]
        with self._conn_access() as conn:
            rows = conn.execute(
                "SELECT cursor, timestamp, content, summary FROM history ORDER BY cursor"
            ).fetchall()
        return [self._row_to_dict(row, cols) for row in rows]

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        cols = ["cursor", "timestamp", "content", "summary"]
        with self._conn_access() as conn:
            rows = conn.execute(
                "SELECT cursor, timestamp, content, summary FROM history WHERE cursor > ? ORDER BY cursor",
                (since_cursor,),
            ).fetchall()
        return [self._row_to_dict(row, cols) for row in rows]

    def compact_history(self, max_entries: int = 1000) -> None:
        with self._conn_access() as conn:
            count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            if count <= max_entries:
                return
            keep_cursors = [
                r[0]
                for r in conn.execute(
                    "SELECT cursor FROM history ORDER BY cursor DESC LIMIT ?", (max_entries,)
                ).fetchall()
            ]
            if not keep_cursors:
                return
            oldest = min(keep_cursors)
            conn.execute("DELETE FROM history WHERE cursor < ?", (oldest,))
            conn.commit()

    def update_summary(self, cursor: int, summary: str) -> None:
        with self._conn_access() as conn:
            conn.execute(
                "UPDATE history SET summary = ? WHERE cursor = ?",
                (summary, cursor),
            )
            conn.commit()

    def history_exists(self) -> bool:
        with self._conn_access() as conn:
            row = conn.execute("SELECT 1 FROM history LIMIT 1").fetchone()
        return row is not None

    # --------------------------------------------------------------------------
    # Sessions + Messages
    # --------------------------------------------------------------------------

    def save_session(self, session: "Session") -> None:
        """Full save: upsert session metadata, delete and re-insert all messages."""
        summary = getattr(session, '_last_summary', None) or ""
        with self._conn_access() as conn:
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO sessions
                       (key, created_at, updated_at, metadata, summary)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        session.key,
                        session.created_at.isoformat(),
                        session.updated_at.isoformat(),
                        json.dumps(session.metadata),
                        summary,
                    ),
                )
                conn.execute("DELETE FROM messages WHERE session_key = ?", (session.key,))
                self._insert_messages(conn, session.key, session.messages)

    @staticmethod
    def _insert_messages(conn: sqlite3.Connection, session_key: str, messages: list[dict[str, Any]]) -> None:
        """Batch-insert messages (no commit — caller owns the transaction)."""
        for msg in messages:
            extra = {k: v for k, v in msg.items() if k not in ("role", "content", "timestamp")}
            content = msg["content"]
            if content is None:
                content = ""
            elif isinstance(content, str):
                content = re.sub(r"[\ud800-\udfff]", "�", content)
            elif isinstance(content, (list, dict)):
                extra["_content_is_json"] = True
                content = json.dumps(content, ensure_ascii=True)
            conn.execute(
                "INSERT INTO messages (session_key, role, content, timestamp, extra) VALUES (?, ?, ?, ?, ?)",
                (
                    session_key,
                    msg["role"],
                    content,
                    msg["timestamp"],
                    json.dumps(extra) if extra else None,
                ),
            )

    def load_session(self, key: str) -> "Session | None":
        from nanobot.session.manager import Session

        with self._conn_access() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at, metadata, summary FROM sessions WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            created_at, updated_at, metadata_json, summary_str = row
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                logger.warning("Corrupt session metadata for {}", key)
                return None
            msg_rows = conn.execute(
                "SELECT role, content, timestamp, extra FROM messages WHERE session_key = ? ORDER BY id",
                (key,),
            ).fetchall()
        messages = []
        for role, content, timestamp, extra in msg_rows:
            msg: dict[str, Any] = {"role": role, "content": content, "timestamp": timestamp}
            if extra:
                try:
                    parsed_extra = json.loads(extra)
                except json.JSONDecodeError:
                    logger.warning("Corrupt message extra in session {}, skipping", key)
                    continue
                if parsed_extra.pop("_content_is_json", False):
                    try:
                        msg["content"] = json.loads(content)
                    except json.JSONDecodeError:
                        logger.warning("Corrupt JSON message content in session {}, skipping", key)
                        continue
                msg.update(parsed_extra)
            messages.append(msg)
        try:
            created = datetime.fromisoformat(created_at)
            updated = datetime.fromisoformat(updated_at)
        except ValueError:
            logger.warning("Corrupt session timestamps for {}", key)
            return None
        session = Session(
            key=key,
            messages=messages,
            created_at=created,
            updated_at=updated,
            metadata=metadata,
        )
        session._last_summary = summary_str or None
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._conn_access() as conn:
            rows = conn.execute(
                "SELECT key, created_at, updated_at, metadata, last_consolidated FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "key": row[0],
                "created_at": row[1],
                "updated_at": row[2],
                "metadata": json.loads(row[3]),
                "last_consolidated": row[4],
            }
            for row in rows
        ]

    def delete_session(self, key: str) -> None:
        with self._conn_access() as conn:
            conn.execute("DELETE FROM sessions WHERE key = ?", (key,))
            conn.commit()

    def sessions_exist(self) -> bool:
        with self._conn_access() as conn:
            row = conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        return row is not None

    @staticmethod
    def _build_content_filter(keyword: str | None) -> tuple[str, list[str]]:
        """Build SQL snippet + args for keyword content filter (supports | OR).

        Returns (sql_clause, args_list). sql_clause is empty if no valid terms.
        """
        if not keyword:
            return "", []
        terms = [t.strip().lower() for t in keyword.split("|") if t.strip()]
        if len(terms) == 1:
            return "AND LOWER(content) LIKE ?", [f"%{terms[0]}%"]
        if len(terms) > 1:
            clauses = ["LOWER(content) LIKE ?" for _ in terms]
            return f"AND ({' OR '.join(clauses)})", [f"%{t}%" for t in terms]
        return "", []

    def search_sessions(
        self,
        keyword: str | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search session messages and archived history by keyword and date range.

        Searches both the current session (messages table) and past archived
        sessions (history table).

        Args:
            keyword: Substring to match in message content (case-insensitive).
                     Use | for OR logic (e.g. 'deploy|rollback').
            start: Start date (YYYY-MM-DD or ISO format), inclusive
            end: End date (YYYY-MM-DD or ISO format), inclusive
            limit: Maximum results to return

        Returns:
            List of dicts with session_key, timestamp, content, role
        """
        # Parse dates
        start_dt = None
        end_dt = None
        if start:
            try:
                start_dt = datetime.fromisoformat(start)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.astimezone()
            except ValueError:
                try:
                    start_dt = datetime.strptime(start, "%Y-%m-%d").astimezone()
                except ValueError:
                    pass
        if end:
            try:
                end_dt = datetime.fromisoformat(end)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.astimezone()
                # Extend to end of day for date-only values
                if "T" not in end:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59)
            except ValueError:
                try:
                    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59
                    ).astimezone()
                except ValueError:
                    pass

        content_clause, content_args = self._build_content_filter(keyword)
        results: list[dict[str, Any]] = []

        # ── Query 1: messages table (current session) ──
        msg_query = "SELECT session_key, role, content, timestamp, extra FROM messages WHERE 1=1"
        msg_args: list[Any] = list(content_args)

        if content_clause:
            msg_query += f" {content_clause}"
        if start_dt:
            msg_query += " AND timestamp >= ?"
            msg_args.append(start_dt.isoformat())
        if end_dt:
            msg_query += " AND timestamp <= ?"
            msg_args.append(end_dt.isoformat())
        msg_query += " ORDER BY timestamp DESC LIMIT ?"
        msg_args.append(limit)

        with self._conn_access() as conn:
            rows = conn.execute(msg_query, msg_args).fetchall()

        for session_key, role, content, timestamp, extra in rows:
            if extra:
                parsed_extra = json.loads(extra)
                if parsed_extra.pop("_content_is_json", False):
                    content = json.loads(content)
            results.append({
                "session_key": session_key,
                "role": role,
                "content": content,
                "timestamp": timestamp,
            })

        # ── Query 2: history table (past archived sessions) ──
        hist_query = "SELECT timestamp, content FROM history WHERE 1=1"
        hist_args: list[Any] = list(content_args)

        if content_clause:
            hist_query += f" {content_clause}"
        if start_dt:
            hist_query += " AND timestamp >= ?"
            hist_args.append(start_dt.isoformat())
        if end_dt:
            hist_query += " AND timestamp <= ?"
            hist_args.append(end_dt.isoformat())
        hist_query += " ORDER BY timestamp DESC LIMIT ?"
        hist_args.append(limit)

        with self._conn_access() as conn:
            hist_rows = conn.execute(hist_query, hist_args).fetchall()

        for timestamp, content in hist_rows:
            results.append({
                "session_key": "history",
                "role": "condensed",
                "content": content,
                "timestamp": timestamp,
            })

        # Sort combined results newest-first, cap at limit
        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results[:limit]

    def close(self) -> None:
        with self._conn_access() as conn:
            conn.close()

    # --------------------------------------------------------------------------
    # Tool Calls
    # --------------------------------------------------------------------------

    _TOOL_CALL_RETENTION_DAYS = 2

    def insert_tool_call(
        self,
        session_key: str,
        iteration: int,
        turn: int,
        tool_name: str,
        params: dict[str, Any] | None = None,
        result: str | None = None,
        success: bool = True,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        self._purge_old_tool_calls()  # Timer-based: only purges once per day max
        if result:
            result = re.sub(r"[\ud800-\udfff]", "�", result)
        if error:
            error = re.sub(r"[\ud800-\udfff]", "�", error)
        result_size = len(result) if result else 0
        with self._conn_access() as conn:
            cursor = conn.execute(
                """INSERT INTO tool_calls
                   (session_key, iteration, turn, tool_name, params, result, result_size, success, error, duration_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_key, iteration, turn, tool_name, json.dumps(params or {}),
                 result, result_size, int(success), error, duration_ms, _utc_now_iso()),
            )
            conn.commit()
        return cursor.lastrowid or 0

    def _purge_old_tool_calls(self) -> None:
        now = datetime.now(timezone.utc)
        # Only purge once per day max to avoid O(N²) on高频 inserts
        if self._last_purge and (now - self._last_purge) < timedelta(days=1):
            return
        self._last_purge = now
        cutoff = (now - timedelta(days=self._TOOL_CALL_RETENTION_DAYS)).isoformat()
        with self._conn_access() as conn:
            deleted = conn.execute(
                "DELETE FROM tool_calls WHERE timestamp < ?", (cutoff,)
            ).rowcount
        if deleted:
            logger.debug("Purged {} old tool call records (>{} days)", deleted, self._TOOL_CALL_RETENTION_DAYS)

    def query_tool_calls(
        self,
        *,
        session_key: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
        min_result_size: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = "SELECT id, session_key, iteration, turn, tool_name, params, result, result_size, success, error, duration_ms, timestamp FROM tool_calls WHERE 1=1"
        args: list[Any] = []
        if session_key:
            query += " AND session_key = ?"
            args.append(session_key)
        if tool_name:
            query += " AND tool_name = ?"
            args.append(tool_name)
        if success is not None:
            query += " AND success = ?"
            args.append(int(success))
        if min_result_size is not None:
            query += " AND result_size >= ?"
            args.append(min_result_size)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        cols = ["id", "session_key", "iteration", "turn", "tool_name", "params",
                "result", "result_size", "success", "error", "duration_ms", "timestamp"]
        with self._conn_access() as conn:
            rows = conn.execute(query, args).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            d = self._row_to_dict(row, cols)
            d["params"] = json.loads(d["params"]) if d["params"] else {}
            results.append(d)
        return results

