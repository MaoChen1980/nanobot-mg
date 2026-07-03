"""Tests for ContextBuilder — self-findings section and general system prompt assembly."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _make_builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(_make_workspace(tmp_path))


# ---------------------------------------------------------------------------
# Regression: past lessons
# ---------------------------------------------------------------------------


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




# ---------------------------------------------------------------------------
# Instructions section (build_instructions_section)
# ---------------------------------------------------------------------------


def test_subagent_instructions_includes_escalation(tmp_path):
    """subagent_escalation.md is loaded in for_subagent=True instructions."""
    builder = _make_builder(tmp_path)
    result = builder.build_instructions_section(for_subagent=True)
    assert "Progress Reporting & Escalation" in result
    assert "notify_orchestrator" in result


def test_subagent_escalation_absent_for_main_agent(tmp_path):
    """Escalation snippet should NOT appear in main agent instructions."""
    builder = _make_builder(tmp_path)
    result = builder.build_instructions_section(for_subagent=False)
    assert "Progress Reporting & Escalation" not in result
