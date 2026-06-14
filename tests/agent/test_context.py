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
# Self-findings section (_build_self_findings_section)
# ---------------------------------------------------------------------------


def test_self_findings_returns_empty_when_no_file(tmp_path):
    """No self_findings.md in framework dir -> returns empty string."""
    builder = _make_builder(tmp_path)
    result = builder._build_self_findings_section()
    assert result == ""


def test_self_findings_reads_content_when_file_exists(tmp_path):
    """self_findings.md exists with content -> returns content."""
    workspace = _make_workspace(tmp_path)
    framework_dir = workspace / "framework"
    framework_dir.mkdir(parents=True)
    findings_file = framework_dir / "self_findings.md"
    findings_file.write_text("## Self-Evolution Findings\n\n- test finding\n", encoding="utf-8")

    builder = ContextBuilder(workspace)
    result = builder._build_self_findings_section()
    assert "Self-Evolution Findings" in result
    assert "test finding" in result


def test_self_findings_returns_empty_when_file_empty(tmp_path):
    """Empty self_findings.md -> returns empty string."""
    workspace = _make_workspace(tmp_path)
    framework_dir = workspace / "framework"
    framework_dir.mkdir(parents=True)
    (framework_dir / "self_findings.md").write_text("   \n\n  ", encoding="utf-8")

    builder = ContextBuilder(workspace)
    result = builder._build_self_findings_section()
    assert result == ""


def test_self_findings_included_in_build_messages(tmp_path):
    """self_findings.md content appears in build_messages output."""
    workspace = _make_workspace(tmp_path)
    framework_dir = workspace / "framework"
    framework_dir.mkdir(parents=True)
    (framework_dir / "self_findings.md").write_text(
        "## Self-Evolution Findings\n\n### abc123 (self_bug)\n**Content**: test bug\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="hello")

    system_content = messages[0]["content"]
    assert "Self-Evolution Findings" in system_content
    assert "abc123" in system_content
    assert "self_bug" in system_content


# ---------------------------------------------------------------------------
# Instructions section (build_instructions_section)
# ---------------------------------------------------------------------------


def test_subagent_instructions_includes_escalation(tmp_path):
    """subagent_escalation.md is loaded in for_subagent=True instructions."""
    builder = _make_builder(tmp_path)
    result = builder.build_instructions_section(for_subagent=True)
    assert "Progress Reporting & Escalation" in result
    assert "notify_orchestrator_tool" in result


def test_subagent_escalation_absent_for_main_agent(tmp_path):
    """Escalation snippet should NOT appear in main agent instructions."""
    builder = _make_builder(tmp_path)
    result = builder.build_instructions_section(for_subagent=False)
    assert "Progress Reporting & Escalation" not in result
