"""Tests for the NanobotDB persistence layer.

Covers facts CRUD, metadata, tool calls, and pruning.
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
# Facts CRUD
# ---------------------------------------------------------------------------

class TestFactsCRUD:
    """upsert_fact, list_facts."""

    def test_insert_and_list(self, db):
        fid = db.upsert_fact(
            "Paris is the capital of France",
            tags=["geography", "capital"],
            source="teacher",
            project="learning",
            confidence=0.95,
        )
        facts = db.list_facts(limit=50)
        assert len(facts) >= 1
        fact = next(f for f in facts if f["id"] == fid)
        assert fact["fact"] == "Paris is the capital of France"
        assert fact["tags"] == ["geography", "capital"]
        assert fact["source"] == "teacher"
        assert fact["project"] == "learning"
        assert fact["confidence"] == 0.95

    def test_upsert_replaces_existing_fact(self, db):
        """INSERT OR REPLACE on duplicate fact text should update all columns."""
        db.upsert_fact("same fact", tags=["old"], source="old_src", confidence=0.3)
        db.upsert_fact("same fact", tags=["new"], source="new_src", confidence=0.9)
        facts = db.list_facts(limit=50)
        matching = [f for f in facts if f["fact"] == "same fact"]
        assert len(matching) == 1
        assert matching[0]["tags"] == ["new"]
        assert matching[0]["source"] == "new_src"
        assert matching[0]["confidence"] == 0.9

    def test_list_filters_by_tag(self, db):
        db.upsert_fact("fact one", tags=["alpha"])
        db.upsert_fact("fact two", tags=["beta"])
        db.upsert_fact("fact three", tags=["alpha", "gamma"])
        alpha_facts = db.list_facts(tag="alpha")
        assert len(alpha_facts) == 2
        assert all("alpha" in f["tags"] for f in alpha_facts)

    def test_list_filters_by_project(self, db):
        db.upsert_fact("fact one", project="proj_a")
        db.upsert_fact("fact two", project="proj_b")
        proj_a_facts = db.list_facts(project="proj_a")
        assert len(proj_a_facts) == 1
        assert proj_a_facts[0]["project"] == "proj_a"

    def test_delete_fact(self, db):
        fid = db.upsert_fact("to delete", tags=["temp"])
        assert len(db.list_facts(limit=50)) == 1
        db.delete_fact(fid)
        assert len(db.list_facts(limit=50)) == 0


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------

class TestToolCalls:
    """insert_tool_call and query_tool_calls."""

    def test_insert_and_query(self, db):
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="read_file", params={"path": "/tmp"})
        db.insert_tool_call("s1", iteration=1, turn=2, tool_name="exec", params={"cmd": "ls"})
        results = db.query_tool_calls(session_key="s1", limit=10)
        assert len(results) == 2
        assert results[0]["tool_name"] == "exec"

    def test_query_filters(self, db):
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="read_file", success=True)
        db.insert_tool_call("s1", iteration=1, turn=2, tool_name="exec", success=False)
        failed = db.query_tool_calls(success=False, limit=10)
        assert len(failed) == 1
        assert failed[0]["tool_name"] == "exec"


# ---------------------------------------------------------------------------
# Pruning / Retention
# ---------------------------------------------------------------------------

class TestPruning:
    """prune_tool_calls."""

    def test_prune_tool_calls_deletes_old_calls(self, db):
        db._conn.execute(
            """INSERT INTO tool_calls (session_key, iteration, turn, tool_name, params, result, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("s_old", 0, 0, "old_tool", "{}", "old_result", "2020-01-01T00:00:00"),
        )
        db._conn.commit()
        db.insert_tool_call("s_new", iteration=1, turn=1, tool_name="recent_tool")
        deleted = db.prune_tool_calls(keep_days=90)
        assert deleted >= 1
        remaining = db.query_tool_calls(limit=50)
        tool_names = [r["tool_name"] for r in remaining]
        assert "old_tool" not in tool_names
        assert "recent_tool" in tool_names

    def test_prune_tool_calls_retains_when_under_threshold(self, db):
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="recent")
        deleted = db.prune_tool_calls(keep_days=90)
        assert deleted == 0
        assert len(db.query_tool_calls(limit=50)) == 1


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
