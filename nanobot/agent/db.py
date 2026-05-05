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


def _row_to_dict(row: tuple, cols: list[str]) -> dict[str, Any]:
    return dict(zip(cols, row, strict=False))


class NanobotDB:
    """SQLite-backed store for history, metadata, sessions, and messages."""

    def __init__(self, db_path: Path | str, *, workspace: Path | str | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._workspace = Path(workspace) if workspace else Path.home() / ".nanobot" / "workspace"
        self._init_tables()
        self.migrate_if_needed()

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
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                project TEXT,
                owner TEXT DEFAULT 'llm',
                description TEXT DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                goal_id TEXT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_goal ON events(goal_id);
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_key);
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

    # --------------------------------------------------------------------------
    # Migration (idempotent — checks metadata.migrated flag)
    # --------------------------------------------------------------------------

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def _history_file(self) -> Path:
        return self.workspace / "memory" / "history.jsonl"

    @property
    def _cursor_file(self) -> Path:
        return self.workspace / "memory" / ".cursor"

    @property
    def _dream_cursor_file(self) -> Path:
        return self.workspace / "memory" / ".dream_cursor"

    @property
    def _sessions_dir(self) -> Path:
        return self.workspace / "sessions"

    def migrate_if_needed(self) -> None:
        migrated = self.get_metadata("migrated") or ""
        done = set(migrated.split(",")) if migrated else set()

        if "history" not in done:
            self._migrate_history()
        if "cursor" not in done:
            self._migrate_cursor()
        if "dream_cursor" not in done:
            self._migrate_dream_cursor()
        if "sessions" not in done:
            self._migrate_sessions()
        if "utc_timestamps" not in done:
            self._migrate_timestamps_to_utc()

        new_done = done | {"history", "sessions", "utc_timestamps"}
        if new_done - done:
            self.set_metadata("migrated", ",".join(sorted(new_done)))

    def _migrate_history(self) -> None:
        path = self._history_file
        if not path.exists():
            return
        count = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO history (cursor, timestamp, content, summary) VALUES (?, ?, ?, '')",
                        (rec["cursor"], rec["timestamp"], rec["content"]),
                    )
                    count += 1
                except Exception:
                    pass
        self._conn.commit()
        logger.info(f"Migrated {count} history entries from {path}")

    def _migrate_cursor(self) -> None:
        path = self._cursor_file
        if not path.exists():
            return
        try:
            cursor = int(path.read_text(encoding="utf-8").strip())
            self.set_metadata("cursor", str(cursor))
            logger.info(f"Migrated cursor={cursor} from {path}")
        except Exception:
            pass

    def _migrate_dream_cursor(self) -> None:
        path = self._dream_cursor_file
        if not path.exists():
            return
        try:
            cursor = int(path.read_text(encoding="utf-8").strip())
            self.set_metadata("dream_cursor", str(cursor))
            logger.info(f"Migrated dream_cursor={cursor} from {path}")
        except Exception:
            pass

    def _migrate_sessions(self) -> None:
        d = self._sessions_dir
        if not d.exists():
            return
        count = 0
        for fpath in d.glob("*.jsonl"):
            key = fpath.stem
            messages: list[dict[str, Any]] = []
            metadata_line: dict[str, Any] | None = None
            try:
                with open(fpath, encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if i == 0 and rec.get("_type") == "metadata":
                            metadata_line = rec
                        else:
                            messages.append(rec)
                if metadata_line is None:
                    continue
                self._conn.execute(
                    """INSERT OR REPLACE INTO sessions
                       (key, created_at, updated_at, metadata, last_consolidated)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        key,
                        metadata_line["created_at"],
                        metadata_line["updated_at"],
                        json.dumps(metadata_line.get("metadata", {})),
                        metadata_line.get("last_consolidated", 0),
                    ),
                )
                for msg in messages:
                    extra = {k: v for k, v in msg.items() if k not in ("role", "content", "timestamp")}
                    self._conn.execute(
                        "INSERT INTO messages (session_key, role, content, timestamp, extra) VALUES (?, ?, ?, ?, ?)",
                        (
                            key,
                            msg["role"],
                            msg["content"],
                            msg["timestamp"],
                            json.dumps(extra) if extra else None,
                        ),
                    )
                count += 1
            except Exception:
                pass
        self._conn.commit()
        logger.info(f"Migrated {count} sessions from {d}")

    def _migrate_timestamps_to_utc(self) -> None:
        """Add +00:00 UTC suffix to naive timestamps missing timezone offset.

        Converts '2026-05-02T16:45:00' → '2026-05-02T16:45:00+00:00'.
        Safe to re-run — only affects timestamps without + or Z suffix.
        """
        tables_columns = [
            ("history", ["timestamp"]),
            ("events", ["timestamp"]),
            ("messages", ["timestamp"]),
            ("sessions", ["created_at", "updated_at"]),
            ("facts", ["created_at", "updated_at"]),
        ]
        for table, cols in tables_columns:
            for col in cols:
                try:
                    cur = self._conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col} NOT LIKE '%+%' AND {col} NOT LIKE '%Z'",
                    ).fetchone()[0]
                    if cur == 0:
                        continue
                    self._conn.execute(
                        f"UPDATE {table} SET {col} = {col} || '+00:00' WHERE {col} NOT LIKE '%+%' AND {col} NOT LIKE '%Z'",
                    )
                    logger.info(f"Migrated {cur} naive timestamps in {table}.{col}")
                except Exception:
                    pass
        self._conn.commit()

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

    def get_dream_cursor(self) -> int:
        val = self.get_metadata("dream_cursor")
        return int(val) if val else 0

    def set_dream_cursor(self, cursor: int) -> None:
        self.set_metadata("dream_cursor", str(cursor))

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
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (key, created_at, updated_at, metadata, last_consolidated)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session.key,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                json.dumps(session.metadata),
                session.last_consolidated,
            ),
        )
        self._conn.execute("DELETE FROM messages WHERE session_key = ?", (session.key,))
        for msg in session.messages:
            extra = {k: v for k, v in msg.items() if k not in ("role", "content", "timestamp")}
            self._conn.execute(
                "INSERT INTO messages (session_key, role, content, timestamp, extra) VALUES (?, ?, ?, ?, ?)",
                (
                    session.key,
                    msg["role"],
                    msg["content"],
                    msg["timestamp"],
                    json.dumps(extra) if extra else None,
                ),
            )
        self._conn.commit()

    def load_session(self, key: str) -> Session | None:
        from dataclasses import replace
        from nanobot.session.manager import Session

        row = self._conn.execute(
            "SELECT created_at, updated_at, metadata, last_consolidated FROM sessions WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        created_at, updated_at, metadata_json, last_consolidated = row
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
            last_consolidated=last_consolidated,
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
    # Goals
    # --------------------------------------------------------------------------

    def upsert_goal(
        self,
        id: str,
        title: str,
        *,
        status: str = "in_progress",
        project: str | None = None,
        owner: str = "llm",
        description: str = "",
        data: dict[str, Any] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        ts = updated_at or _utc_now_iso()
        created = created_at or ts
        self._conn.execute(
            """INSERT OR REPLACE INTO goals (id, title, status, project, owner, description, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, title, status, project, owner, description, json.dumps(data or {}), created, ts),
        )
        self._conn.commit()

    def get_goal(self, id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, title, status, project, owner, description, data, created_at, updated_at FROM goals WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "title": row[1], "status": row[2], "project": row[3],
            "owner": row[4], "description": row[5], "data": json.loads(row[6]),
            "created_at": row[7], "updated_at": row[8],
        }

    def list_goals(self, status: str | None = None, project: str | None = None, scope: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, title, status, project, owner, description, data, created_at, updated_at FROM goals WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY updated_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        goals = [
            {
                "id": r[0], "title": r[1], "status": r[2], "project": r[3],
                "owner": r[4], "description": r[5], "data": json.loads(r[6]),
                "created_at": r[7], "updated_at": r[8],
            }
            for r in rows
        ]
        # Filter by scope if specified (from data.scopes array)
        if scope:
            goals = [g for g in goals if scope in g.get("data", {}).get("scopes", [])]
        return goals

    def delete_goal(self, id: str) -> None:
        self._conn.execute("DELETE FROM goals WHERE id = ?", (id,))
        self._conn.commit()

    # --------------------------------------------------------------------------
    # Events
    # --------------------------------------------------------------------------

    def insert_event(
        self,
        event_type: str,
        content: str,
        *,
        session_key: str | None = None,
        goal_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> int:
        ts = timestamp or _utc_now_iso()
        cursor = self._conn.execute(
            """INSERT INTO events (session_key, timestamp, event_type, goal_id, content, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_key, ts, event_type, goal_id, content, json.dumps(tags or []), json.dumps(metadata or {})),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

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

    def list_events(
        self,
        *,
        goal_id: str | None = None,
        session_key: str | None = None,
        event_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = "SELECT id, session_key, timestamp, event_type, goal_id, content, tags, metadata FROM events WHERE 1=1"
        params: list[Any] = []
        if goal_id:
            query += " AND goal_id = ?"
            params.append(goal_id)
        if session_key:
            query += " AND session_key = ?"
            params.append(session_key)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0], "session_key": r[1], "timestamp": r[2], "event_type": r[3],
                "goal_id": r[4], "content": r[5], "tags": json.loads(r[6]), "metadata": json.loads(r[7]),
            }
            for r in rows
        ]

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
            """INSERT INTO facts (fact, tags, source, project, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fact, json.dumps(tags or []), source, project, confidence, ts),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

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
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')
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
