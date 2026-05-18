"""Tests for lesson injection in system prompts."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_lessons_are_included_when_file_exists(tmp_path) -> None:
    """When tasks/lessons.md exists with content, it should appear in system prompt."""
    workspace = _make_workspace(tmp_path)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "lessons.md").write_text(
        "- Always check error codes\n- Use async/await consistently\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" in prompt
    assert "Always check error codes" in prompt


def test_lessons_omitted_when_file_missing(tmp_path) -> None:
    """When tasks/lessons.md does not exist, no lessons section should appear."""
    workspace = _make_workspace(tmp_path)

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt


def test_lessons_omitted_when_file_is_empty(tmp_path) -> None:
    """When tasks/lessons.md is empty, no lessons section should appear."""
    workspace = _make_workspace(tmp_path)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "lessons.md").write_text("", encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt
