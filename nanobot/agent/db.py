"""SQLite persistence layer for nanobot runtime data."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with timezone offset."""
    return datetime.now(timezone.utc).isoformat()


class NanobotDB:
    """SQLite-backed store for history, metadata, sessions, and messages."""

    def __init__(self, db_path: Path | str, *, workspace: Path | str | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._workspace = Path(workspace) if workspace else Path.home() / ".nanobot" / "workspace"
        self._init_tables()

    # --------------------------------------------------------------------------
    # Schema init
    # --------------------------------------------------------------------------

    def _init_tables(self) -> None:
        self._conn.executescript("""
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
        """)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply backward-compatible schema migrations for existing databases."""
        self._conn.commit()

    @property
    def workspace(self) -> Path:
        return self._workspace

    # --------------------------------------------------------------------------
    # Metadata
    # --------------------------------------------------------------------------

    def get_metadata(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # --------------------------------------------------------------------------
    # History
    # --------------------------------------------------------------------------

    def append_history(self, content: str, *, timestamp: str | None = None, summary: str = "") -> int:
        ts = timestamp or _utc_now_iso()
        cursor = self._next_cursor()
        self._conn.execute(
            "INSERT INTO history (cursor, timestamp, content, summary) VALUES (?, ?, ?, ?)",
            (cursor, ts, content, summary),
        )
        self._conn.commit()
        self.set_metadata("cursor", str(cursor))
        return cursor

    def _next_cursor(self) -> int:
        row = self._conn.execute("SELECT MAX(cursor) FROM history").fetchone()
        return (row[0] or 0) + 1

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
        rows = self._conn.execute(
            "SELECT cursor, timestamp, content, summary FROM history ORDER BY cursor"
        ).fetchall()
        return [_row_to_dict(row, cols) for row in rows]

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        cols = ["cursor", "timestamp", "content", "summary"]
        rows = self._conn.execute(
            "SELECT cursor, timestamp, content, summary FROM history WHERE cursor > ? ORDER BY cursor",
            (since_cursor,),
        ).fetchall()
        return [_row_to_dict(row, cols) for row in rows]

    def compact_history(self, max_entries: int = 1000) -> None:
        count = self._conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if count <= max_entries:
            return
        keep_cursors = [
            r[0]
            for r in self._conn.execute(
                "SELECT cursor FROM history ORDER BY cursor DESC LIMIT ?", (max_entries,)
            ).fetchall()
        ]
        if not keep_cursors:
            return
        oldest = min(keep_cursors)
        self._conn.execute("DELETE FROM history WHERE cursor < ?", (oldest,))
        self._conn.commit()

    def update_summary(self, cursor: int, summary: str) -> None:
        self._conn.execute(
            "UPDATE history SET summary = ? WHERE cursor = ?",
            (summary, cursor),
        )
        self._conn.commit()

    def history_exists(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM history LIMIT 1").fetchone()
        return row is not None

    # --------------------------------------------------------------------------
    # Sessions + Messages
    # --------------------------------------------------------------------------

    def save_session(self, session: Session) -> None:
        """Full save: upsert session metadata, delete and re-insert all messages."""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (key, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?)""",
                (
                    session.key,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    json.dumps(session.metadata),
                ),
            )
            self._conn.execute("DELETE FROM messages WHERE session_key = ?", (session.key,))
            self._insert_messages(session.key, session.messages)

    def _insert_messages(self, session_key: str, messages: list[dict[str, Any]]) -> None:
        """Batch-insert messages (no commit — caller owns the transaction)."""
        for msg in messages:
            extra = {k: v for k, v in msg.items() if k not in ("role", "content", "timestamp")}
            self._conn.execute(
                "INSERT INTO messages (session_key, role, content, timestamp, extra) VALUES (?, ?, ?, ?, ?)",
                (
                    session_key,
                    msg["role"],
                    msg["content"],
                    msg["timestamp"],
                    json.dumps(extra) if extra else None,
                ),
            )

    def load_session(self, key: str) -> Session | None:
        from dataclasses import replace
        from nanobot.session.manager import Session

        row = self._conn.execute(
            "SELECT created_at, updated_at, metadata FROM sessions WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        created_at, updated_at, metadata_json = row
        metadata = json.loads(metadata_json)
        msg_rows = self._conn.execute(
            "SELECT role, content, timestamp, extra FROM messages WHERE session_key = ? ORDER BY id",
            (key,),
        ).fetchall()
        messages = []
        for role, content, timestamp, extra in msg_rows:
            msg: dict[str, Any] = {"role": role, "content": content, "timestamp": timestamp}
            if extra:
                msg.update(json.loads(extra))
            messages.append(msg)
        return Session(
            key=key,
            messages=messages,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            metadata=metadata,
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
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
        self._conn.execute("DELETE FROM sessions WHERE key = ?", (key,))
        self._conn.commit()

    def sessions_exist(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------------------------
    # Tool Calls
    # --------------------------------------------------------------------------

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
        result_size = len(result) if result else 0
        cursor = self._conn.execute(
            """INSERT INTO tool_calls
               (session_key, iteration, turn, tool_name, params, result, result_size, success, error, duration_ms, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_key, iteration, turn, tool_name, json.dumps(params or {}),
             result, result_size, int(success), error, duration_ms, _utc_now_iso()),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

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
            query += " AND result_size > ?"
            args.append(min_result_size)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(query, args).fetchall()
        cols = ["id", "session_key", "iteration", "turn", "tool_name", "params", "result", "result_size", "success", "error", "duration_ms", "timestamp"]
        return [dict(zip(cols, row)) for row in rows]

    def prune_tool_calls(self, keep_days: int = 90) -> int:
        self._conn.execute("PRAGMA foreign_keys = OFF")
        cursor = self._conn.execute(
            "DELETE FROM tool_calls WHERE timestamp < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.commit()
        return cursor.rowcount

    # --------------------------------------------------------------------------
    # Facts
    # --------------------------------------------------------------------------

    def upsert_fact(
        self,
        fact: str,
        *,
        tags: list[str] | None = None,
        source: str | None = None,
        project: str | None = None,
        confidence: float = 1.0,
    ) -> int:
        ts = _utc_now_iso()
        cursor = self._conn.execute(
            """INSERT OR REPLACE INTO facts (fact, tags, source, project, confidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fact, json.dumps(tags or []), source, project, confidence, ts, ts),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def delete_fact(self, fact_id: int) -> None:
        self._conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._conn.commit()

    def list_facts(
        self,
        *,
        project: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT id, fact, tags, source, project, created_at, updated_at, confidence FROM facts WHERE 1=1"
        params: list[Any] = []
        if project:
            query += " AND project = ?"
            params.append(project)
        if tag:
            query += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)"
            params.append(tag)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0], "fact": r[1], "tags": json.loads(r[2]), "source": r[3],
                "project": r[4], "created_at": r[5], "updated_at": r[6], "confidence": r[7],
            }
            for r in rows
        ]
