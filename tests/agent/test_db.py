"""Tests for the NanobotDB persistence layer.

Covers metadata, tool calls, sessions, and messages.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nanobot.agent.db import NanobotDB
from nanobot.session.manager import Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Provide a fresh file-backed NanobotDB for each test."""
    _db = NanobotDB(tmp_path / "test.db")
    yield _db
    _db.close()


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------

class TestToolCalls:
    """insert_tool_call and query_tool_calls."""

    def test_insert_and_query(self, db):
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="read_file_tool", params={"path": "/tmp"})
        db.insert_tool_call("s1", iteration=1, turn=2, tool_name="exec_tool", params={"cmd": "ls"})
        results = db.query_tool_calls(session_key="s1", limit=10)
        assert len(results) == 2
        assert results[0]["tool_name"] == "exec_tool"

    def test_query_filters(self, db):
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="read_file_tool", success=True)
        db.insert_tool_call("s1", iteration=1, turn=2, tool_name="exec_tool", success=False)
        failed = db.query_tool_calls(success=False, limit=10)
        assert len(failed) == 1
        assert failed[0]["tool_name"] == "exec_tool"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    """get_metadata and set_metadata."""

    def test_set_and_get(self, db):
        db.set_metadata("key1", "value1")
        assert db.get_metadata("key1") == "value1"

    def test_get_nonexistent(self, db):
        assert db.get_metadata("nonexistent") is None

    def test_replace_existing(self, db):
        db.set_metadata("key1", "original")
        db.set_metadata("key1", "replaced")
        assert db.get_metadata("key1") == "replaced"


# ---------------------------------------------------------------------------
# Session corruption resilience
# ---------------------------------------------------------------------------

class TestSessionCorruption:
    """load_session handles corrupt data gracefully."""

    def test_corrupt_metadata(self, db):
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        db.save_session(s)
        with db._conn_access() as conn:
            conn.execute("UPDATE sessions SET metadata = '{bad json' WHERE key = 'ch:u'")
        assert db.load_session("ch:u") is None

    def test_corrupt_message_extra(self, db):
        s = Session(key="ch:u")
        s.add_message("tool", "result", tool_call_id="call_1")
        db.save_session(s)
        with db._conn_access() as conn:
            conn.execute("UPDATE messages SET extra = '{bad json' WHERE session_key = 'ch:u'")
        loaded = db.load_session("ch:u")
        assert loaded is not None
        assert len(loaded.messages) == 0

    def test_corrupt_json_content(self, db):
        s = Session(key="ch:u")
        s.add_message("user", ["list", "content"])
        db.save_session(s)
        with db._conn_access() as conn:
            conn.execute("UPDATE messages SET content = '{bad json' WHERE session_key = 'ch:u'")
        loaded = db.load_session("ch:u")
        assert loaded is not None
        assert len(loaded.messages) == 0

    def test_corrupt_timestamps(self, db):
        s = Session(key="ch:u")
        s.add_message("user", "hi")
        db.save_session(s)
        with db._conn_access() as conn:
            conn.execute("UPDATE sessions SET created_at = 'not-a-date' WHERE key = 'ch:u'")
        assert db.load_session("ch:u") is None


# ---------------------------------------------------------------------------
# DB init resilience
# ---------------------------------------------------------------------------

class TestDbInit:
    """NanobotDB.__init__ resilience against corrupt files."""

    def test_corrupt_db_file_raises(self, tmp_path):
        db_path = tmp_path / "corrupt.db"
        db_path.write_bytes(b"this is not a valid sqlite file\x00\x00")
        with pytest.raises(sqlite3.DatabaseError):
            NanobotDB(db_path)

    def test_creates_parent_dir(self, tmp_path):
        db_path = tmp_path / "nonexistent" / "sub.db"
        _db = NanobotDB(db_path)
        _db.close()
        assert db_path.exists()
