"""Tests for ContextBuilder task tree — _render_tree_items and _build_task_tree_section."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _make_builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(_make_workspace(tmp_path))


def _write_tree(tmp_path: Path, items: list[dict]) -> Path:
    workspace = _make_workspace(tmp_path)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "tree.json").write_text(
        json.dumps({"schema_version": 1, "items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return workspace


# ---------------------------------------------------------------------------
# _render_tree_items — pure static method
# ---------------------------------------------------------------------------


class TestRenderTreeItems:
    def test_empty_items_returns_empty_string(self):
        assert ContextBuilder._render_tree_items([], parent=None) == ""

    def test_single_root_renders_correctly(self):
        items = [
            {"id": "root", "name": "Root Task", "status": "active", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- ○ **Root Task** [active]" in result

    def test_completed_root_shows_checkmark(self):
        items = [
            {"id": "root", "name": "Root Task", "status": "completed", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- ✅ **Root Task** [completed]" in result

    def test_failed_root_shows_fail_mark(self):
        items = [
            {"id": "root", "name": "Root Task", "status": "failed", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- ✗ **Root Task** [failed]" in result

    def test_paused_root_shows_pause_mark(self):
        items = [
            {"id": "root", "name": "Root Task", "status": "paused", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- ⏸ **Root Task** [paused]" in result

    def test_pending_root_shows_pending_mark(self):
        items = [
            {"id": "root", "name": "Root Task", "status": "pending", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- · **Root Task** [pending]" in result

    def test_unknown_status_falls_back_to_active_mark(self):
        items = [
            {"id": "root", "name": "Root", "status": "unknown", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "- ○ **Root** [unknown]" in result

    def test_item_with_doc_path(self):
        items = [
            {"id": "root", "name": "Root", "status": "active", "parent": None, "doc": "tasks/root/report.md"},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "→ tasks/root/report.md" in result

    def test_item_with_note(self):
        items = [
            {"id": "root", "name": "Root", "status": "failed", "parent": None,
             "note": "Blocked by external dependency"},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "└ note: Blocked by external dependency" in result

    def test_item_with_doc_and_note(self):
        items = [
            {"id": "root", "name": "Root", "status": "active", "parent": None,
             "doc": "tasks/root/doc.md", "note": "WIP"},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "→ tasks/root/doc.md" in result
        assert "└ note: WIP" in result

    def test_parent_child_indentation(self):
        items = [
            {"id": "root", "name": "Root", "status": "active", "parent": None},
            {"id": "child", "name": "Child", "status": "completed", "parent": "root"},
        ]
        result = ContextBuilder._render_tree_items(items)
        lines = [l for l in result.split("\n") if l.strip()]
        root_line = [l for l in lines if "Root" in l][0]
        child_line = [l for l in lines if "Child" in l][0]
        assert not root_line.startswith("  "), "Root should not be indented"
        assert child_line.startswith("  "), "Child should be indented"

    def test_deep_nesting(self):
        items = [
            {"id": "l0", "name": "Level 0", "status": "active", "parent": None},
            {"id": "l1", "name": "Level 1", "status": "active", "parent": "l0"},
            {"id": "l2", "name": "Level 2", "status": "completed", "parent": "l1"},
        ]
        result = ContextBuilder._render_tree_items(items)
        lines = [l for l in result.split("\n") if l.strip()]
        l0_line = [l for l in lines if "Level 0" in l][0]
        l1_line = [l for l in lines if "Level 1" in l][0]
        l2_line = [l for l in lines if "Level 2" in l][0]
        assert not l0_line.startswith("  ")
        assert l1_line.startswith("  ")
        assert l2_line.startswith("    "), "Level 2 should be double indented"

    def test_roots_sorted_before_children(self):
        items = [
            {"id": "child", "name": "Child", "status": "active", "parent": "root"},
            {"id": "root", "name": "Root", "status": "active", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        root_pos = result.index("Root")
        child_pos = result.index("Child")
        assert root_pos < child_pos, "Root should appear before child"

    def test_multiple_roots_both_rendered(self):
        items = [
            {"id": "r1", "name": "Project A", "status": "active", "parent": None},
            {"id": "r2", "name": "Project B", "status": "completed", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "Project A" in result
        assert "Project B" in result

    def test_child_without_name_uses_id(self):
        items = [
            {"id": "root", "name": "Root", "status": "active", "parent": None},
            {"id": "orphan-child", "parent": "root", "status": "active"},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "orphan-child" in result

    def test_missing_status_defaults_to_active(self):
        items = [
            {"id": "root", "name": "Root", "parent": None},
        ]
        result = ContextBuilder._render_tree_items(items)
        assert "○" in result

    def test_multiple_children_same_parent(self):
        items = [
            {"id": "root", "name": "Root", "status": "active", "parent": None},
            {"id": "a", "name": "A", "status": "completed", "parent": "root", "created": "2026-01-01"},
            {"id": "b", "name": "B", "status": "pending", "parent": "root", "created": "2026-01-02"},
        ]
        result = ContextBuilder._render_tree_items(items)
        lines = [l for l in result.split("\n") if l.strip()]
        pos_a = next(i for i, l in enumerate(lines) if "**A**" in l)
        pos_b = next(i for i, l in enumerate(lines) if "**B**" in l)
        assert pos_a < pos_b, "Children should be sorted by created date"


# ---------------------------------------------------------------------------
# _build_task_tree_section — reads from disk and renders
# ---------------------------------------------------------------------------


class TestBuildTaskTreeSection:
    def test_returns_empty_when_no_tree_file(self, tmp_path):
        builder = _make_builder(tmp_path)
        assert builder._build_task_tree_section() == ""

    def test_returns_empty_when_empty_items(self, tmp_path):
        workspace = _write_tree(tmp_path, [])
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_returns_empty_when_invalid_json(self, tmp_path):
        workspace = _make_workspace(tmp_path)
        (workspace / "tasks").mkdir(parents=True)
        (workspace / "tasks" / "tree.json").write_text("not json", encoding="utf-8")
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_returns_empty_when_empty_json_file(self, tmp_path):
        workspace = _make_workspace(tmp_path)
        (workspace / "tasks").mkdir(parents=True)
        (workspace / "tasks" / "tree.json").write_text("", encoding="utf-8")
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_renders_single_root(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "proj", "name": "My Project", "status": "active", "parent": None,
             "criteria": "Done", "created": "2026-06-18", "updated": "2026-06-18"},
        ])
        builder = ContextBuilder(workspace)
        result = builder._build_task_tree_section()
        assert "Task Tree" in result
        assert "My Project" in result
        assert "tree.json" in result
        assert "tree.schema.md" in result

    def test_header_contains_schema_reference(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "r", "name": "R", "status": "active", "parent": None,
             "criteria": "x", "created": "2026-01-01", "updated": "2026-01-01"},
        ])
        builder = ContextBuilder(workspace)
        result = builder._build_task_tree_section()
        assert "Schema reference: tasks/tree.schema.md" in result

    def test_full_tree_rendering(self, tmp_path):
        """Integration: root with child renders both levels correctly."""
        workspace = _write_tree(tmp_path, [
            {"id": "root", "name": "Root", "status": "active", "parent": None,
             "criteria": "All done", "created": "2026-06-01", "updated": "2026-06-18"},
            {"id": "c1", "name": "Step 1", "status": "completed", "parent": "root",
             "criteria": "Step done", "created": "2026-06-01", "updated": "2026-06-10",
             "completed": "2026-06-10"},
            {"id": "c2", "name": "Step 2", "status": "active", "parent": "root",
             "criteria": "Step done", "created": "2026-06-01", "updated": "2026-06-18",
             "note": "Waiting on review"},
        ])
        builder = ContextBuilder(workspace)
        result = builder._build_task_tree_section()
        assert "- ○ **Root** [active]" in result
        assert "- ✅ **Step 1** [completed]" in result
        assert "- ○ **Step 2** [active]" in result
        assert "Waiting on review" in result


# ---------------------------------------------------------------------------
# build_instructions_section — task_tree injection control
# ---------------------------------------------------------------------------


class TestTaskTreeInInstructions:
    def test_task_tree_included_for_agent(self, tmp_path):
        """Main agent instructions include Task Tree System."""
        builder = _make_builder(tmp_path)
        result = builder.build_instructions_section(for_subagent=False)
        assert "### Task Tree System" in result

    def test_task_tree_excluded_for_subagent(self, tmp_path):
        """Subagent instructions must NOT include Task Tree System."""
        builder = _make_builder(tmp_path)
        result = builder.build_instructions_section(for_subagent=True)
        assert "### Task Tree System" not in result


# ---------------------------------------------------------------------------
# Tree data in build_messages output
# ---------------------------------------------------------------------------


class TestTaskTreeInMessages:
    def test_rendered_tree_in_instructions_section(self, tmp_path):
        """build_instructions_section should include rendered task tree for main agent."""
        workspace = _write_tree(tmp_path, [
            {"id": "root", "name": "Integration Test", "status": "active",
             "parent": None, "criteria": "x", "created": "2026-01-01",
             "updated": "2026-01-01"},
        ])
        builder = ContextBuilder(workspace)
        result = builder.build_instructions_section(for_subagent=False)
        assert "Integration Test" in result
        assert "Task Tree" in result


# ---------------------------------------------------------------------------
# _has_active_tasks — terminal vs active detection
# ---------------------------------------------------------------------------


class TestHasActiveTasks:
    def _builder(self) -> ContextBuilder:
        """Return a bare builder for calling instance methods."""
        return object.__new__(ContextBuilder)

    def test_empty_list_returns_false(self):
        assert self._builder()._has_active_tasks([]) is False

    def test_single_active_returns_true(self):
        items = [{"id": "r1", "status": "active", "parent": None}]
        assert self._builder()._has_active_tasks(items) is True

    def test_single_pending_returns_true(self):
        items = [{"id": "r1", "status": "pending", "parent": None}]
        assert self._builder()._has_active_tasks(items) is True

    def test_single_paused_returns_true(self):
        items = [{"id": "r1", "status": "paused", "parent": None}]
        assert self._builder()._has_active_tasks(items) is True

    def test_single_completed_returns_false(self):
        items = [{"id": "r1", "status": "completed", "parent": None}]
        assert self._builder()._has_active_tasks(items) is False

    def test_single_failed_returns_false(self):
        items = [{"id": "r1", "status": "failed", "parent": None}]
        assert self._builder()._has_active_tasks(items) is False

    def test_missing_status_treated_as_active(self):
        """Missing/null status is active, consistent with _render_tree_items."""
        items = [{"id": "r1", "parent": None}]
        assert self._builder()._has_active_tasks(items) is True

    def test_all_completed_returns_false(self):
        items = [
            {"id": "r1", "status": "completed", "parent": None},
            {"id": "r2", "status": "completed", "parent": None},
        ]
        assert self._builder()._has_active_tasks(items) is False

    def test_mixed_terminals_and_active_returns_true(self):
        items = [
            {"id": "r1", "status": "completed", "parent": None},
            {"id": "r2", "status": "active", "parent": "r1"},
        ]
        assert self._builder()._has_active_tasks(items) is True

    def test_deep_tree_with_active_leaf(self):
        items = [
            {"id": "l0", "status": "completed", "parent": None},
            {"id": "l1", "status": "completed", "parent": "l0"},
            {"id": "l2", "status": "active", "parent": "l1"},
        ]
        assert self._builder()._has_active_tasks(items) is True

    def test_deep_tree_all_completed(self):
        items = [
            {"id": "l0", "status": "completed", "parent": None},
            {"id": "l1", "status": "completed", "parent": "l0"},
            {"id": "l2", "status": "completed", "parent": "l1"},
        ]
        assert self._builder()._has_active_tasks(items) is False

    def test_unknown_status_treated_as_active(self):
        items = [{"id": "r1", "status": "unknown", "parent": None}]
        assert self._builder()._has_active_tasks(items) is True


# ---------------------------------------------------------------------------
# _build_task_tree_section — all-completed early return
# ---------------------------------------------------------------------------


class TestBuildTaskTreeSectionAllCompleted:
    def test_all_completed_returns_empty(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "r1", "status": "completed", "parent": None},
            {"id": "c1", "status": "completed", "parent": "r1"},
        ])
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_all_failed_returns_empty(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "r1", "status": "failed", "parent": None},
        ])
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_mixed_completed_and_failed_all_terminal_returns_empty(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "r1", "status": "completed", "parent": None},
            {"id": "c1", "status": "failed", "parent": "r1"},
        ])
        builder = ContextBuilder(workspace)
        assert builder._build_task_tree_section() == ""

    def test_any_active_returns_tree(self, tmp_path):
        workspace = _write_tree(tmp_path, [
            {"id": "r1", "status": "completed", "parent": None},
            {"id": "c1", "status": "active", "parent": "r1"},
        ])
        builder = ContextBuilder(workspace)
        result = builder._build_task_tree_section()
        assert "Task Tree" in result
        assert "c1" in result
