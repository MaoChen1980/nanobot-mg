"""Tests for tools_index.py caching behavior."""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.tools_index import rebuild_tools_index, _tools_index_cache


def _make_tool(workspace: Path, name: str, readme_content: str | None = None) -> Path:
    """Create a tool directory with optional readme."""
    tool_dir = workspace / "tools" / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    if readme_content is not None:
        (tool_dir / "readme.md").write_text(readme_content, encoding="utf-8")
    return tool_dir


# ---------------------------------------------------------------------------
# Basic functionality (regression — signature changed from None to str)
# ---------------------------------------------------------------------------


def test_returns_string(tmp_path):
    """rebuild_tools_index returns content string (regression: was None)."""
    content = rebuild_tools_index(tmp_path)
    assert isinstance(content, str)
    assert "# Tool Usage Notes" in content


def test_writes_tools_md(tmp_path):
    """TOOLS.md is written to disk."""
    rebuild_tools_index(tmp_path)
    tools_md = tmp_path / "TOOLS.md"
    assert tools_md.exists()
    assert "# Tool Usage Notes" in tools_md.read_text(encoding="utf-8")


def test_includes_installed_tools(tmp_path):
    """Installed tools appear in the generated index."""
    _make_tool(tmp_path, "my-tool", "# My Tool — does awesome things\n\nUsage: ...")
    content = rebuild_tools_index(tmp_path)
    assert "my-tool" in content
    assert "My Tool" in content
    assert "does awesome things" in content


# ---------------------------------------------------------------------------
# Cache hit — repeated calls
# ---------------------------------------------------------------------------


def test_cache_hit_does_not_rewrite(tmp_path):
    """Repeated calls with unchanged tools/ return cached content."""
    content1 = rebuild_tools_index(tmp_path)

    tools_md = tmp_path / "TOOLS.md"
    original_mtime = tools_md.stat().st_mtime

    content2 = rebuild_tools_index(tmp_path)
    assert content1 == content2
    assert tools_md.stat().st_mtime == original_mtime


def test_cache_hit_empty_tools_dir(tmp_path):
    """Empty tools/ directory is cached correctly."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)

    content1 = rebuild_tools_index(tmp_path)
    content2 = rebuild_tools_index(tmp_path)
    assert content1 == content2

    tools_md = tmp_path / "TOOLS.md"
    original_mtime = tools_md.stat().st_mtime
    rebuild_tools_index(tmp_path)
    assert tools_md.stat().st_mtime == original_mtime


# ---------------------------------------------------------------------------
# Cache invalidation — tools directory changes
# ---------------------------------------------------------------------------


def test_cache_invalidated_by_new_tool(tmp_path):
    """Adding a new tool dir invalidates cache."""
    content_before = rebuild_tools_index(tmp_path)
    assert "new-tool" not in content_before

    _make_tool(tmp_path, "new-tool", "# New Tool — fresh\n\nFresh tool.")
    content_after = rebuild_tools_index(tmp_path)
    assert "new-tool" in content_after
    assert content_before != content_after


def test_cache_invalidated_by_modified_readme(tmp_path):
    """Modifying a tool's readme.md invalidates cache."""
    _make_tool(tmp_path, "test-tool", "# Old Description\n\nOld content")
    content_before = rebuild_tools_index(tmp_path)
    assert "Old Description" in content_before

    (tmp_path / "tools" / "test-tool" / "readme.md").write_text(
        "# New Description\n\nNew content", encoding="utf-8"
    )
    content_after = rebuild_tools_index(tmp_path)
    assert "New Description" in content_after
    assert "Old Description" not in content_after


def test_cache_invalidated_by_removed_tool(tmp_path):
    """Removing a tool dir invalidates cache."""
    _make_tool(tmp_path, "removable-tool", "# Removable\n\nWill be removed.")
    content_before = rebuild_tools_index(tmp_path)
    assert "removable-tool" in content_before

    import shutil
    shutil.rmtree(tmp_path / "tools" / "removable-tool")
    content_after = rebuild_tools_index(tmp_path)
    assert "removable-tool" not in content_after
    assert content_before != content_after


# ---------------------------------------------------------------------------
# Cache isolation — different workspaces
# ---------------------------------------------------------------------------


def test_different_workspaces_independent_cache(tmp_path):
    """Different workspace paths have independent cache entries."""
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir(parents=True)
    ws2.mkdir(parents=True)

    _make_tool(ws1, "tool-a", "# Tool A\n\nIn workspace 1")
    _make_tool(ws2, "tool-b", "# Tool B\n\nIn workspace 2")

    content1 = rebuild_tools_index(ws1)
    content2 = rebuild_tools_index(ws2)
    assert "tool-a" in content1
    assert "tool-b" not in content1
    assert "tool-b" in content2
    assert "tool-a" not in content2


# ---------------------------------------------------------------------------
# Regression — file still written correctly
# ---------------------------------------------------------------------------


def test_tools_md_content_correct_after_cache(tmp_path):
    """TOOLS.md on disk matches returned content."""
    _make_tool(tmp_path, "cli-tool", "# CLI Tool\n\nA command-line helper.")
    content = rebuild_tools_index(tmp_path)
    disk_content = (tmp_path / "TOOLS.md").read_text(encoding="utf-8")
    assert content == disk_content


def test_cache_clear_then_rebuild_still_correct(tmp_path):
    """After clearing cache, rebuild still produces correct output."""
    _make_tool(tmp_path, "persistent-tool", "# Persistent\n\nSurvives cache clear.")
    content1 = rebuild_tools_index(tmp_path)
    _tools_index_cache.clear()
    content2 = rebuild_tools_index(tmp_path)
    assert content1 == content2
    assert "persistent-tool" in content2
