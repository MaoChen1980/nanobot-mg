"""Comprehensive tests for the NanobotDB persistence layer.

Tests cover upsert_goal merge semantics, CRUD for dependencies/lessons/events/facts,
pruning/retention, and list_goals filtering.
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
# Upsert-Goal Merge Semantics
# ---------------------------------------------------------------------------

class TestUpsertGoalMerge:
    """Verify the merge logic when upserting goals (lines ~380-417)."""

    def test_creates_new_goal_when_not_existing(self, db):
        """Upsert on a non-existing id should create a full new row."""
        db.upsert_goal(
            id="g_new",
            title="Brand new",
            status="in_progress",
            project="nanobot",
            bot="main",
            owner="llm",
            description="fresh goal",
            data={"key": "val"},
            priority=3,
            deadline="2025-12-31",
            parent_id=None,
            tags=["urgent"],
            source="test",
        )
        goal = db.get_goal("g_new")
        assert goal is not None
        assert goal["title"] == "Brand new"
        assert goal["status"] == "in_progress"
        assert goal["project"] == "nanobot"
        assert goal["bot"] == "main"
        assert goal["owner"] == "llm"
        assert goal["description"] == "fresh goal"
        assert goal["data"] == {"key": "val"}
        assert goal["priority"] == 3
        assert goal["deadline"] == "2025-12-31"
        assert goal["tags"] == ["urgent"]
        assert goal["source"] == "test"
        assert goal["created_at"] == goal["updated_at"]

    def test_preserves_unset_fields_on_existing_goal(self, db):
        """Upsert partial fields should keep existing values for unset fields."""
        db.upsert_goal(
            id="g1", title="Original", status="in_progress",
            project="nanobot", bot="main", description="desc",
            data={"a": 1}, priority=5, deadline="2025-06-01",
            parent_id="parent", tags=["important"], source="manual",
        )
        # Re-upsert with only title and status provided (others fall back to defaults)
        db.upsert_goal(id="g1", title="Updated", status="completed")
        goal = db.get_goal("g1")
        assert goal["title"] == "Updated"  # provided
        assert goal["status"] == "completed"  # provided
        assert goal["project"] == "nanobot"  # preserved
        assert goal["bot"] == "main"  # preserved
        assert goal["description"] == "desc"  # preserved
        assert goal["data"] == {"a": 1}  # preserved
        assert goal["priority"] == 5  # preserved
        assert goal["deadline"] == "2025-06-01"  # preserved
        assert goal["parent_id"] == "parent"  # preserved
        assert goal["tags"] == ["important"]  # preserved
        assert goal["source"] == "manual"  # preserved

    def test_empty_string_title_does_not_overwrite(self, db):
        """empty-string title should be treated as 'not provided' and keep existing."""
        db.upsert_goal(id="g1", title="Keep Me")
        db.upsert_goal(id="g1", title="")
        goal = db.get_goal("g1")
        assert goal["title"] == "Keep Me"

    def test_merges_data_dicts(self, db):
        """Provided data dict should be shallow-merged into existing data."""
        db.upsert_goal(id="g1", title="MergeMe", data={"a": 1, "b": 2})
        db.upsert_goal(id="g1", title="MergeMe", data={"b": 3, "c": 4})
        goal = db.get_goal("g1")
        assert goal["data"] == {"a": 1, "b": 3, "c": 4}

    def test_priority_zero_does_not_overwrite(self, db):
        """priority=0 is the default signal and should not overwrite an existing
        non-zero priority."""
        db.upsert_goal(id="g1", title="Pri", priority=7)
        db.upsert_goal(id="g1", title="Pri", priority=0)
        goal = db.get_goal("g1")
        assert goal["priority"] == 7

    def test_deadline_none_does_not_overwrite(self, db):
        """deadline=None should preserve an existing deadline value."""
        db.upsert_goal(id="g1", title="DeadlineTest", deadline="2025-12-31")
        db.upsert_goal(id="g1", title="DeadlineTest", deadline=None)
        goal = db.get_goal("g1")
        assert goal["deadline"] == "2025-12-31"

    def test_owner_empty_string_does_not_overwrite(self, db):
        """empty-string owner should not replace existing owner value."""
        db.upsert_goal(id="g1", title="OwnerTest", owner="human")
        db.upsert_goal(id="g1", title="OwnerTest", owner="")
        goal = db.get_goal("g1")
        assert goal["owner"] == "human"

    def test_created_at_preserved_on_update(self, db):
        """created_at should never change when re-upserting."""
        db.upsert_goal(id="g1", title="CreatedAt", created_at="2020-01-01T00:00:00")
        original_created = db.get_goal("g1")["created_at"]
        db.upsert_goal(id="g1", title="CreatedAt Updated")
        goal = db.get_goal("g1")
        assert goal["created_at"] == original_created

    def test_tags_none_preserves_existing(self, db):
        """tags=None should keep existing tags list."""
        db.upsert_goal(id="g1", title="TagsTest", tags=["a", "b"])
        db.upsert_goal(id="g1", title="TagsTest", tags=None)
        goal = db.get_goal("g1")
        assert goal["tags"] == ["a", "b"]

    def test_tags_explicit_replaces(self, db):
        """Providing a new tags list should replace the old one."""
        db.upsert_goal(id="g1", title="TagsTest", tags=["a", "b"])
        db.upsert_goal(id="g1", title="TagsTest", tags=["c"])
        goal = db.get_goal("g1")
        assert goal["tags"] == ["c"]

    def test_parent_id_none_preserves_existing(self, db):
        """parent_id=None should preserve existing parent_id."""
        db.upsert_goal(id="g1", title="ParentTest", parent_id="parent_goal")
        db.upsert_goal(id="g1", title="ParentTest", parent_id=None)
        goal = db.get_goal("g1")
        assert goal["parent_id"] == "parent_goal"

    def test_source_empty_string_does_not_overwrite(self, db):
        """empty-string source should preserve existing source."""
        db.upsert_goal(id="g1", title="Src", source="original_source")
        db.upsert_goal(id="g1", title="Src", source="")
        goal = db.get_goal("g1")
        assert goal["source"] == "original_source"


# ---------------------------------------------------------------------------
# Dependencies CRUD
# ---------------------------------------------------------------------------

class TestDependenciesCRUD:
    """insert_dependency, list_dependencies, delete_dependency."""

    def _create_goals(self, db, *ids: str) -> None:
        for gid in ids:
            db.upsert_goal(id=gid, title=gid)

    def test_insert_and_list(self, db):
        self._create_goals(db, "g1", "g2", "g3")
        d1 = db.insert_dependency("g1", "g2")
        d2 = db.insert_dependency("g1", "g3", dep_type="triggers")
        deps = db.list_dependencies("g1")
        assert len(deps) == 2
        ids = {d["depends_on"] for d in deps}
        assert ids == {"g2", "g3"}
        types = {(d["depends_on"], d["dep_type"]) for d in deps}
        assert ("g2", "blocks") in types
        assert ("g3", "triggers") in types
        # Verify IDs are returned
        returned_ids = {d["id"] for d in deps}
        assert returned_ids == {d1, d2}

    def test_list_empty(self, db):
        self._create_goals(db, "g1")
        assert db.list_dependencies("g1") == []

    def test_delete_dependency(self, db):
        self._create_goals(db, "g1", "g2")
        dep_id = db.insert_dependency("g1", "g2")
        assert len(db.list_dependencies("g1")) == 1
        db.delete_dependency(dep_id)
        assert db.list_dependencies("g1") == []

    def test_list_dependents(self, db):
        """list_dependents should return goals that depend on a given goal."""
        self._create_goals(db, "g1", "g2", "g3")
        db.insert_dependency("g1", "g3")
        db.insert_dependency("g2", "g3")
        deps = db.list_dependents("g3")
        assert len(deps) == 2
        assert {d["goal_id"] for d in deps} == {"g1", "g2"}

    def test_list_blocked_goals(self, db):
        """list_blocked_goals should return goals whose dependency is not completed."""
        self._create_goals(db, "task_a", "task_b", "task_c")
        db.upsert_goal(id="task_c", title="task_c", status="completed")
        db.insert_dependency("task_a", "task_b")
        db.insert_dependency("task_b", "task_c")
        blocked = db.list_blocked_goals()
        blocked_ids = {b["id"] for b in blocked}
        assert "task_a" in blocked_ids  # depends on task_b which is not completed
        assert "task_b" not in blocked_ids  # depends on task_c which IS completed


# ---------------------------------------------------------------------------
# Lessons CRUD
# ---------------------------------------------------------------------------

class TestLessonsCRUD:
    """insert_lesson, list_lessons, delete_lesson."""

    def test_insert_and_list(self, db):
        db.upsert_goal(id="g1", title="Goal 1")
        l1 = db.insert_lesson("success", "It worked", goal_id="g1", detail="details", tags=["tag1"])
        l2 = db.insert_lesson("failure", "It broke", goal_id="g1")
        lessons = db.list_lessons(limit=50)
        assert len(lessons) >= 2
        ids = {l["id"] for l in lessons}
        assert l1 in ids and l2 in ids
        # Verify fields
        lesson1 = next(l for l in lessons if l["id"] == l1)
        assert lesson1["lesson_type"] == "success"
        assert lesson1["summary"] == "It worked"
        assert lesson1["detail"] == "details"
        assert lesson1["tags"] == ["tag1"]
        assert lesson1["goal_id"] == "g1"

    def test_list_filters_by_goal_id(self, db):
        db.upsert_goal(id="g1", title="G1")
        db.upsert_goal(id="g2", title="G2")
        db.insert_lesson("a", "summary a", goal_id="g1")
        db.insert_lesson("b", "summary b", goal_id="g2")
        g1_lessons = db.list_lessons(goal_id="g1")
        assert len(g1_lessons) == 1
        assert g1_lessons[0]["goal_id"] == "g1"

    def test_list_filters_by_lesson_type(self, db):
        db.upsert_goal(id="g1", title="G1")
        db.insert_lesson("type_a", "summary a", goal_id="g1")
        db.insert_lesson("type_b", "summary b", goal_id="g1")
        type_a_only = db.list_lessons(lesson_type="type_a")
        assert len(type_a_only) == 1
        assert type_a_only[0]["lesson_type"] == "type_a"

    def test_delete_lesson(self, db):
        db.upsert_goal(id="g1", title="G1")
        lid = db.insert_lesson("test", "to delete", goal_id="g1")
        assert len(db.list_lessons(limit=50)) == 1
        db.delete_lesson(lid)
        assert db.list_lessons(limit=50) == []

    def test_list_respects_limit(self, db):
        db.upsert_goal(id="g1", title="G1")
        for i in range(5):
            db.insert_lesson("type", f"summary {i}", goal_id="g1")
        results = db.list_lessons(limit=3)
        assert len(results) == 3

    def test_increment_applied_count(self, db):
        db.upsert_goal(id="g1", title="G1")
        lid = db.insert_lesson("tip", "useful tip", goal_id="g1")
        db.increment_lesson_applied(lid)
        lessons = db.list_lessons(limit=50)
        lesson = next(l for l in lessons if l["id"] == lid)
        assert lesson["applied_count"] == 1
        db.increment_lesson_applied(lid)
        lessons = db.list_lessons(limit=50)
        lesson = next(l for l in lessons if l["id"] == lid)
        assert lesson["applied_count"] == 2


# ---------------------------------------------------------------------------
# Events CRUD
# ---------------------------------------------------------------------------

class TestEventsCRUD:
    """insert_event, list_events, delete_event."""

    def test_insert_and_list(self, db):
        db.upsert_goal(id="g1", title="G1")
        eid = db.insert_event(
            "milestone",
            "Reached a milestone",
            goal_id="g1",
            session_key="s1",
            tags=["important"],
            metadata={"key": "value"},
        )
        events = db.list_events(limit=50)
        assert len(events) >= 1
        ev = next(e for e in events if e["id"] == eid)
        assert ev["event_type"] == "milestone"
        assert ev["content"] == "Reached a milestone"
        assert ev["goal_id"] == "g1"
        assert ev["session_key"] == "s1"
        assert ev["tags"] == ["important"]
        assert ev["metadata"] == {"key": "value"}

    def test_list_filters_by_event_type(self, db):
        db.upsert_goal(id="g1", title="G1")
        db.insert_event("type_a", "event a", goal_id="g1")
        db.insert_event("type_b", "event b", goal_id="g1")
        filtered = db.list_events(event_type="type_a")
        assert len(filtered) == 1
        assert filtered[0]["event_type"] == "type_a"

    def test_list_filters_by_goal_id(self, db):
        db.upsert_goal(id="g1", title="G1")
        db.upsert_goal(id="g2", title="G2")
        db.insert_event("type_a", "event for g1", goal_id="g1")
        db.insert_event("type_b", "event for g2", goal_id="g2")
        filtered = db.list_events(goal_id="g1")
        assert len(filtered) == 1
        assert filtered[0]["goal_id"] == "g1"

    def test_list_respects_limit(self, db):
        db.upsert_goal(id="g1", title="G1")
        for i in range(5):
            db.insert_event("test", f"event {i}", goal_id="g1")
        results = db.list_events(limit=3)
        assert len(results) == 3

    def test_list_filters_by_session_key(self, db):
        db.upsert_goal(id="g1", title="G1")
        db.insert_event("test", "session 1 event", goal_id="g1", session_key="s1")
        db.insert_event("test", "session 2 event", goal_id="g1", session_key="s2")
        filtered = db.list_events(session_key="s1")
        assert len(filtered) == 1
        assert filtered[0]["session_key"] == "s1"

    def test_delete_event(self, db):
        db.upsert_goal(id="g1", title="G1")
        eid = db.insert_event("test", "to delete", goal_id="g1")
        assert len(db.list_events(limit=50)) == 1
        db.delete_event(eid)
        assert db.list_events(limit=50) == []


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
# Pruning / Retention
# ---------------------------------------------------------------------------

class TestPruning:
    """prune_events and prune_tool_calls."""

    def test_prune_events_deletes_old_events(self, db):
        """Events older than keep_days should be removed; recent ones stay."""
        db.upsert_goal(id="g1", title="G1")
        # Insert an event with a very old timestamp
        db.insert_event("test", "old event", goal_id="g1", timestamp="2020-01-01T00:00:00")
        # Insert an event with current timestamp
        db.insert_event("test", "recent event", goal_id="g1")
        deleted = db.prune_events(keep_days=90)
        assert deleted >= 1
        remaining = db.list_events(limit=50)
        contents = [e["content"] for e in remaining]
        assert "old event" not in contents
        assert "recent event" in contents

    def test_prune_events_retains_when_under_threshold(self, db):
        """All events within keep_days should be kept."""
        db.upsert_goal(id="g1", title="G1")
        db.insert_event("test", "recent event", goal_id="g1")
        deleted = db.prune_events(keep_days=90)
        assert deleted == 0
        assert len(db.list_events(limit=50)) == 1

    def test_prune_tool_calls_deletes_old_calls(self, db):
        """Tool calls older than keep_days should be removed."""
        # Insert old tool calls via raw SQL (API always uses current timestamp)
        db._conn.execute(
            """INSERT INTO tool_calls (session_key, iteration, turn, tool_name, params, result, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("s_old", 0, 0, "old_tool", "{}", "old_result", "2020-01-01T00:00:00"),
        )
        db._conn.commit()
        # Insert recent tool call via the API
        db.insert_tool_call("s_new", iteration=1, turn=1, tool_name="recent_tool")
        deleted = db.prune_tool_calls(keep_days=90)
        assert deleted >= 1
        remaining = db.query_tool_calls(limit=50)
        tool_names = [r["tool_name"] for r in remaining]
        assert "old_tool" not in tool_names
        assert "recent_tool" in tool_names

    def test_prune_tool_calls_retains_when_under_threshold(self, db):
        """All tool calls within keep_days should be kept."""
        db.insert_tool_call("s1", iteration=1, turn=1, tool_name="recent")
        deleted = db.prune_tool_calls(keep_days=90)
        assert deleted == 0
        assert len(db.query_tool_calls(limit=50)) == 1


# ---------------------------------------------------------------------------
# list_goals filtering
# ---------------------------------------------------------------------------

class TestListGoalsFiltering:
    """Filtering and sorting of list_goals."""

    def _create_goal(self, db, gid: str, **kwargs) -> None:
        params = {"id": gid, "title": gid}
        params.update(kwargs)
        db.upsert_goal(**params)

    def test_filter_by_status(self, db):
        self._create_goal(db, "g1", status="completed")
        self._create_goal(db, "g2", status="in_progress")
        self._create_goal(db, "g3", status="completed")
        completed = db.list_goals(status="completed")
        assert len(completed) == 2
        assert all(g["status"] == "completed" for g in completed)

    def test_filter_by_project(self, db):
        self._create_goal(db, "g1", project="nanobot")
        self._create_goal(db, "g2", project="openclaw")
        self._create_goal(db, "g3", project="nanobot")
        nb = db.list_goals(project="nanobot")
        assert len(nb) == 2
        assert all(g["project"] == "nanobot" for g in nb)

    def test_filter_by_bot(self, db):
        self._create_goal(db, "g1", bot="alice")
        self._create_goal(db, "g2", bot="bob")
        self._create_goal(db, "g3", bot="alice")
        alice = db.list_goals(bot="alice")
        assert len(alice) == 2
        assert all(g["bot"] == "alice" for g in alice)

    def test_combined_filters(self, db):
        self._create_goal(db, "g1", status="completed", project="nanobot", bot="alice")
        self._create_goal(db, "g2", status="completed", project="openclaw", bot="alice")
        self._create_goal(db, "g3", status="in_progress", project="nanobot", bot="alice")
        self._create_goal(db, "g4", status="completed", project="nanobot", bot="bob")
        results = db.list_goals(status="completed", project="nanobot", bot="alice")
        assert len(results) == 1
        assert results[0]["id"] == "g1"

    def test_scope_filter(self, db):
        """Scope filter (in-memory, data.scopes) should work."""
        self._create_goal(db, "g1", data={"scopes": ["memory"]})
        self._create_goal(db, "g2", data={"scopes": ["agent/loop"]})
        self._create_goal(db, "g3", data={"scopes": ["memory", "agent/loop"]})
        results = db.list_goals(scope="memory")
        assert {g["id"] for g in results} == {"g1", "g3"}

    def test_limit_applied(self, db):
        """list_goals hard-codes LIMIT 500 in the SQL query."""
        ts = "2025-01-01T00:00:00"
        # Bulk-insert 502 goals directly via raw SQL for speed
        for i in range(502):
            db._conn.execute(
                "INSERT INTO goals (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (f"bulk_{i}", f"Bulk Goal {i}", ts, ts),
            )
        db._conn.commit()
        results = db.list_goals()
        assert len(results) == 500

    def test_sort_by_priority_desc_default(self, db):
        """Default sort is updated_at DESC, but priority DESC is also available."""
        self._create_goal(db, "g_low", priority=1)
        self._create_goal(db, "g_high", priority=10)
        self._create_goal(db, "g_mid", priority=5)
        results = db.list_goals(sort_by="priority", sort_desc=True)
        prios = [g["priority"] for g in results[:3]]
        assert prios == [10, 5, 1]

    def test_filter_by_status_no_match(self, db):
        """Filtering by a status that no goal has returns empty list."""
        self._create_goal(db, "g1", status="in_progress")
        assert db.list_goals(status="archived") == []

    def test_filter_by_project_no_match(self, db):
        """Filtering by a project that no goal has returns empty list."""
        self._create_goal(db, "g1", project="nanobot")
        assert db.list_goals(project="nonexistent") == []


# ---------------------------------------------------------------------------
# Goal retrieval
# ---------------------------------------------------------------------------

class TestGetGoal:
    """get_goal edge cases."""

    def test_get_nonexistent_goal(self, db):
        assert db.get_goal("does_not_exist") is None

    def test_get_goal_returns_full_data(self, db):
        db.upsert_goal(
            id="g1", title="Full", status="completed",
            project="test", bot="bot1", owner="llm",
            description="full desc", data={"num": 42},
            priority=3, deadline="2025-12-31",
            parent_id="p1", tags=["a", "b"], source="src",
        )
        goal = db.get_goal("g1")
        assert goal["id"] == "g1"
        assert goal["data"] == {"num": 42}
        assert goal["tags"] == ["a", "b"]
        assert goal["source"] == "src"

    def test_get_goal_empty_data_defaults(self, db):
        """A goal created with minimal fields should get sensible defaults."""
        db.upsert_goal(id="g1", title="Minimal")
        goal = db.get_goal("g1")
        assert goal["status"] == "in_progress"
        assert goal["owner"] == "llm"
        assert goal["data"] == {}
        assert goal["priority"] == 0
        assert goal["tags"] == []
        assert goal["source"] == ""
        assert goal["project"] is None
        assert goal["bot"] is None
        assert goal["deadline"] is None
        assert goal["parent_id"] is None


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
# Delete goal
# ---------------------------------------------------------------------------

class TestDeleteGoal:
    """delete_goal and cascade behavior."""

    def test_delete_goal_removes_it(self, db):
        db.upsert_goal(id="g1", title="To delete")
        assert db.get_goal("g1") is not None
        db.delete_goal("g1")
        assert db.get_goal("g1") is None

    def test_delete_nonexistent_goal_does_not_raise(self, db):
        db.delete_goal("nonexistent")  # should not raise
