"""Tests for ContextBuilder memory section — truncation and inlining."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


def _make_memory_file(workspace: Path, name: str, content: str) -> None:
    mem_dir = workspace / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / name).write_text(content, encoding="utf-8")


def test_memory_section_short_file_passed_through(tmp_path):
    """Files under 2000 chars are included in full."""
    ws = _make_workspace(tmp_path)
    _make_memory_file(ws, "user.md", "Language: Chinese\nTimezone: UTC+8")
    builder = ContextBuilder(ws)
    section = builder._build_memory_section()
    assert "Language: Chinese" in section
    assert "Timezone: UTC+8" in section
    assert "truncated" not in section


def test_memory_section_large_file_truncated(tmp_path):
    """Files over 2000 chars are truncated with a note."""
    ws = _make_workspace(tmp_path)
    _make_memory_file(ws, "system.md", "x" * 2500)
    builder = ContextBuilder(ws)
    section = builder._build_memory_section()
    assert "(truncated, see file in memory/)" in section
    assert len(section) < 2500


def test_memory_section_both_files_inlined(tmp_path):
    """Both system.md and user.md appear in output."""
    ws = _make_workspace(tmp_path)
    _make_memory_file(ws, "system.md", "System rule: be concise")
    _make_memory_file(ws, "user.md", "User prefers bullet points")
    builder = ContextBuilder(ws)
    section = builder._build_memory_section()
    assert "System" in section
    assert "User" in section
    assert "be concise" in section
    assert "bullet points" in section


def test_memory_section_empty_when_no_files(tmp_path):
    """No memory files → empty section."""
    ws = _make_workspace(tmp_path)
    builder = ContextBuilder(ws)
    section = builder._build_memory_section()
    assert section == ""


def test_memory_section_missing_one_file_still_shows_other(tmp_path):
    """Only one file exists → still shows content."""
    ws = _make_workspace(tmp_path)
    _make_memory_file(ws, "user.md", "User is admin")
    builder = ContextBuilder(ws)
    section = builder._build_memory_section()
    assert "User" in section
    assert "admin" in section
    assert "System" not in section
