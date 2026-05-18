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
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                project TEXT,
                bot TEXT,
                owner TEXT DEFAULT 'llm',
                description TEXT DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                deadline TEXT,
                parent_id TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS task_dependencies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                depends_on TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                dep_type TEXT NOT NULL DEFAULT 'blocks',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_deps_goal ON task_dependencies(goal_id);
            CREATE INDEX IF NOT EXISTS idx_deps_depends ON task_dependencies(depends_on);
            CREATE TABLE IF NOT EXISTS task_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
                lesson_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                applied_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_lessons_type ON task_lessons(lesson_type);
            CREATE INDEX IF NOT EXISTS idx_lessons_goal ON task_lessons(goal_id);
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
            CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_unique ON facts(fact);
            CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
            CREATE INDEX IF NOT EXISTS idx_goals_project ON goals(project);
            CREATE INDEX IF NOT EXISTS idx_goals_bot ON goals(bot);
            CREATE INDEX IF NOT EXISTS idx_goals_updated ON goals(updated_at);
            CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
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
        for col, ddl in [
            ("priority", "ALTER TABLE goals ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"),
            ("deadline", "ALTER TABLE goals ADD COLUMN deadline TEXT"),
            ("parent_id", "ALTER TABLE goals ADD COLUMN parent_id TEXT"),
            ("tags", "ALTER TABLE goals ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"),
            ("source", "ALTER TABLE goals ADD COLUMN source TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
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
    # Goals
    # --------------------------------------------------------------------------

    def upsert_goal(
        self,
        id: str,
        title: str,
        *,
        status: str = "in_progress",
        project: str | None = None,
        bot: str | None = None,
        owner: str = "llm",
        description: str = "",
        data: dict[str, Any] | None = None,
        priority: int = 0,
        deadline: str | None = None,
        parent_id: str | None = None,
        tags: list[str] | None = None,
        source: str = "",
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        ts = updated_at or _utc_now_iso()
        created = created_at or ts

        existing = self.get_goal(id)
        if existing:
            # Merge: provided values override existing, preserving unset fields
            # (default param values = "not explicitly provided" signal)
            merged = {
                "title": title or existing["title"],
                "status": status if status != "pending" else existing.get("status", "pending"),
                "project": project if project is not None else existing.get("project"),
                "bot": bot if bot is not None else existing.get("bot"),
                "owner": owner if owner else existing.get("owner", "llm"),
                "description": description if description else existing.get("description", ""),
                "data": json.dumps({**existing.get("data", {}), **(data or {})}),
                "priority": priority if priority != 0 else existing.get("priority", 0),
                "deadline": deadline if deadline is not None else existing.get("deadline"),
                "parent_id": parent_id if parent_id is not None else existing.get("parent_id"),
                "tags": json.dumps(tags if tags is not None else existing.get("tags", [])),
                "source": source if source else existing.get("source", ""),
                "created_at": existing["created_at"],  # preserve original
            }
            self._conn.execute(
                """UPDATE goals SET title=:title, status=:status, project=:project,
                   bot=:bot, owner=:owner, description=:description, data=:data,
                   updated_at=:updated_at, priority=:priority, deadline=:deadline,
                   parent_id=:parent_id, tags=:tags, source=:source
                   WHERE id=:id""",
                {"id": id, "updated_at": ts, **merged},
            )
        else:
            merged_tags = json.dumps(tags or [])
            self._conn.execute(
                """INSERT INTO goals
                   (id, title, status, project, bot, owner, description, data,
                    created_at, updated_at, priority, deadline, parent_id, tags, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, title, status, project, bot, owner, description, json.dumps(data or {}),
                 created, ts, priority, deadline, parent_id, merged_tags, source),
            )
        self._conn.commit()

    _GOAL_COLS = (
        "id, title, status, project, bot, owner, description, data, "
        "created_at, updated_at, priority, deadline, parent_id, tags, source"
    )

    def get_goal(self, id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT {self._GOAL_COLS} FROM goals WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_goal(row)

    def _row_to_goal(self, row: tuple) -> dict[str, Any]:
        return {
            "id": row[0], "title": row[1], "status": row[2], "project": row[3],
            "bot": row[4], "owner": row[5], "description": row[6], "data": json.loads(row[7]),
            "created_at": row[8], "updated_at": row[9],
            "priority": row[10], "deadline": row[11], "parent_id": row[12],
            "tags": json.loads(row[13]) if row[13] else [],
            "source": row[14] or "",
        }

    def list_goals(
        self,
        status: str | None = None,
        project: str | None = None,
        scope: str | None = None,
        bot: str | None = None,
        *,
        sort_by: str = "updated_at",
        sort_desc: bool = True,
    ) -> list[dict[str, Any]]:
        query = f"SELECT {self._GOAL_COLS} FROM goals WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if project:
            query += " AND project = ?"
            params.append(project)
        if bot:
            query += " AND bot = ?"
            params.append(bot)
        if sort_by in ("updated_at", "priority", "created_at", "deadline"):
            query += f" ORDER BY {sort_by} {'DESC' if sort_desc else 'ASC'}"
        else:
            query += " ORDER BY updated_at DESC"
        query += " LIMIT 500"
        rows = self._conn.execute(query, params).fetchall()
        goals = [self._row_to_goal(r) for r in rows]
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
    # Retention / pruning
    # --------------------------------------------------------------------------

    def prune_events(self, keep_days: int = 90) -> int:
        """Delete events older than keep_days. Returns count deleted."""
        self._conn.execute("PRAGMA foreign_keys = OFF")
        cursor = self._conn.execute(
            "DELETE FROM events WHERE timestamp < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.commit()
        return cursor.rowcount

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
    # Task Dependencies
    # --------------------------------------------------------------------------

    def insert_dependency(self, goal_id: str, depends_on: str, dep_type: str = "blocks") -> int:
        cursor = self._conn.execute(
            """INSERT INTO task_dependencies (goal_id, depends_on, dep_type, created_at)
               VALUES (?, ?, ?, ?)""",
            (goal_id, depends_on, dep_type, _utc_now_iso()),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def list_dependencies(self, goal_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, goal_id, depends_on, dep_type, created_at FROM task_dependencies WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()
        return [
            {"id": r[0], "goal_id": r[1], "depends_on": r[2], "dep_type": r[3], "created_at": r[4]}
            for r in rows
        ]

    def list_dependents(self, goal_id: str) -> list[dict[str, Any]]:
        """List goals that depend on this goal."""
        rows = self._conn.execute(
            "SELECT id, goal_id, depends_on, dep_type, created_at FROM task_dependencies WHERE depends_on = ?",
            (goal_id,),
        ).fetchall()
        return [
            {"id": r[0], "goal_id": r[1], "depends_on": r[2], "dep_type": r[3], "created_at": r[4]}
            for r in rows
        ]

    def delete_dependency(self, id: int) -> None:
        self._conn.execute("DELETE FROM task_dependencies WHERE id = ?", (id,))
        self._conn.commit()

    def list_blocked_goals(self) -> list[dict[str, Any]]:
        """List goals whose dependencies are not yet met (depends_on goal not completed)."""
        rows = self._conn.execute(
            """SELECT DISTINCT g.id, g.title, g.status
               FROM goals g
               JOIN task_dependencies d ON d.goal_id = g.id
               JOIN goals dep ON dep.id = d.depends_on
               WHERE dep.status != 'completed'
               AND g.status != 'completed'
               AND g.status != 'archived'
               ORDER BY g.priority DESC"""
        ).fetchall()
        return [{"id": r[0], "title": r[1], "status": r[2]} for r in rows]

    # --------------------------------------------------------------------------
    # Task Lessons
    # --------------------------------------------------------------------------

    def insert_lesson(
        self,
        lesson_type: str,
        summary: str,
        *,
        goal_id: str | None = None,
        detail: str = "",
        tags: list[str] | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """INSERT INTO task_lessons (goal_id, lesson_type, summary, detail, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (goal_id, lesson_type, summary, detail, json.dumps(tags or []), _utc_now_iso()),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def list_lessons(
        self,
        *,
        lesson_type: str | None = None,
        goal_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = "SELECT id, goal_id, lesson_type, summary, detail, tags, created_at, applied_count FROM task_lessons WHERE 1=1"
        params: list[Any] = []
        if lesson_type:
            query += " AND lesson_type = ?"
            params.append(lesson_type)
        if goal_id:
            query += " AND goal_id = ?"
            params.append(goal_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0], "goal_id": r[1], "lesson_type": r[2], "summary": r[3],
                "detail": r[4], "tags": json.loads(r[5]) if r[5] else [],
                "created_at": r[6], "applied_count": r[7],
            }
            for r in rows
        ]

    def increment_lesson_applied(self, lesson_id: int) -> None:
        self._conn.execute(
            "UPDATE task_lessons SET applied_count = applied_count + 1 WHERE id = ?",
            (lesson_id,),
        )
        self._conn.commit()

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

    def delete_lesson(self, lesson_id: int) -> None:
        self._conn.execute("DELETE FROM task_lessons WHERE id = ?", (lesson_id,))
        self._conn.commit()

    def delete_event(self, event_id: int) -> None:
        self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
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
