#!/usr/bin/env python3
"""Standalone migration script: migrate legacy JSONL files to SQLite.

Idempotent — checks metadata.migrated flag and skips if already done.
Can be run independently or via NanobotDB.migrate_if_needed().

Usage:
    python scripts/migrate_to_sqlite.py [--dry-run] [--force]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def get_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            cursor INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT ''
        );
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
    conn.commit()
    return conn


def migrate_history(conn: sqlite3.Connection, workspace: Path) -> int:
    history_file = workspace / "memory" / "history.jsonl"
    if not history_file.exists():
        return 0
    count = 0
    with open(history_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                conn.execute(
                    "INSERT OR IGNORE INTO history (cursor, timestamp, content, summary) VALUES (?, ?, ?, '')",
                    (rec["cursor"], rec["timestamp"], rec["content"]),
                )
                count += 1
            except Exception:
                pass
    conn.commit()
    return count


def migrate_cursors(conn: sqlite3.Connection, workspace: Path) -> tuple[int, int]:
    cursor_file = workspace / "memory" / ".cursor"
    dream_file = workspace / "memory" / ".dream_cursor"
    cursor_val = 0
    dream_val = 0
    if cursor_file.exists():
        try:
            cursor_val = int(cursor_file.read_text(encoding="utf-8").strip())
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('cursor', ?)",
                (str(cursor_val),),
            )
        except Exception:
            pass
    if dream_file.exists():
        try:
            dream_val = int(dream_file.read_text(encoding="utf-8").strip())
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('dream_cursor', ?)",
                (str(dream_val),),
            )
        except Exception:
            pass
    if cursor_val or dream_val:
        conn.commit()
    return cursor_val, dream_val


def migrate_sessions(conn: sqlite3.Connection, workspace: Path) -> int:
    sessions_dir = workspace / "sessions"
    if not sessions_dir.exists():
        return 0
    count = 0
    for fpath in sessions_dir.glob("*.jsonl"):
        key = fpath.stem
        messages: list[dict] = []
        metadata_line: dict | None = None
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
            conn.execute(
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
                conn.execute(
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
    conn.commit()
    return count


def get_workspace() -> Path:
    return Path.home() / ".nanobot" / "workspace"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    db_path = Path.home() / ".nanobot" / "nanobot.db"
    workspace = get_workspace()

    if not db_path.exists() and not dry_run:
        print(f"DB not found at {db_path}, nothing to migrate (run nanobot gateway first)")
        print("To run a dry-run migration check, use: python scripts/migrate_to_sqlite.py --dry-run")
        return

    if dry_run:
        print(f"[DRY RUN] Would migrate data from {workspace}")
        print(f"  DB: {db_path}")
        return

    conn = get_db(db_path)

    migrated = conn.execute(
        "SELECT value FROM metadata WHERE key = 'migrated'"
    ).fetchone()
    done = set(migrated[0].split(",")) if migrated and migrated[0] else set()

    history_count = migrate_history(conn, workspace) if "history" not in done else 0
    cursor_val, dream_val = migrate_cursors(conn, workspace) if "history" not in done else (0, 0)
    session_count = migrate_sessions(conn, workspace) if "sessions" not in done else 0

    new_done = done | {"history", "sessions"}
    if new_done - done:
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', ?)",
            (",".join(sorted(new_done)),),
        )
        conn.commit()

    conn.close()

    print(f"Migration complete:")
    print(f"  history entries: {history_count}")
    print(f"  cursor: {cursor_val}, dream_cursor: {dream_val}")
    print(f"  sessions: {session_count}")
    print(f"  DB: {db_path}")
    print(f"  Old files preserved in place")


if __name__ == "__main__":
    main()
