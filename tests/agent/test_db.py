"""Tests for the NanobotDB persistence layer.

Covers metadata, tool calls, sessions, and messages.
"""

from __future__ import annotations

import json
import pytest

from nanobot.agent.db import NanobotDB


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
