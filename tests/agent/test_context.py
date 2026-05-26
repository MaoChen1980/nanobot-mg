"""Tests confirming Past Lessons is no longer injected in system prompts."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_lessons_never_injected(tmp_path) -> None:
    """Past Lessons should never appear in system prompt, even if file exists."""
    workspace = _make_workspace(tmp_path)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "lessons.md").write_text(
        "- Always check error codes\n- Use async/await consistently\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt


def test_lessons_omitted_when_file_missing(tmp_path) -> None:
    """Sanity check: no Past Lessons when file doesn't exist."""
    workspace = _make_workspace(tmp_path)

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Past Lessons" not in prompt
