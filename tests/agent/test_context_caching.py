"""Tests for ContextBuilder caching behavior."""

from __future__ import annotations

import os
import time
from pathlib import Path

from nanobot.utils.tools_index import rebuild_tools_index as _rebuild_tools_index

from nanobot.agent.context import ContextBuilder
import nanobot.agent.context as ctx_module


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _make_builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(_make_workspace(tmp_path))


def _write_file(path: Path, content: str) -> None:
    """Write a file and ensure mtime changes (Windows has coarse mtime granularity)."""
    path.write_text(content, encoding="utf-8")
    new_mtime = time.time() + 1
    os.utime(path, (new_mtime, new_mtime))


# ---------------------------------------------------------------------------
# _cached_read_text
# ---------------------------------------------------------------------------


def test_cached_read_text_returns_none_for_missing(tmp_path):
    """Non-existent file returns None."""
    builder = _make_builder(tmp_path)
    result = builder._cached_read_text(tmp_path / "nonexistent.md")
    assert result is None


def test_cached_read_text_reads_file(tmp_path):
    """First call reads and returns file content."""
    builder = _make_builder(tmp_path)
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    result = builder._cached_read_text(f)
    assert result == "hello"


def test_cached_read_text_invalidated_by_mtime(tmp_path):
    """Changing file mtime forces a re-read."""
    builder = _make_builder(tmp_path)
    f = tmp_path / "test.txt"
    f.write_text("version1", encoding="utf-8")

    result1 = builder._cached_read_text(f)
    assert result1 == "version1"

    _write_file(f, "version2")

    result2 = builder._cached_read_text(f)
    assert result2 == "version2"


def test_cached_read_text_cache_hit_returns_same_object(tmp_path):
    """Cache hit returns exact same string object (no re-read)."""
    builder = _make_builder(tmp_path)
    f = tmp_path / "data.txt"
    f.write_text("cached content", encoding="utf-8")

    result1 = builder._cached_read_text(f)
    result2 = builder._cached_read_text(f)
    assert result1 is result2


def test_cached_read_text_empty_file_returns_empty_string(tmp_path):
    """Empty file returns empty string (not None)."""
    builder = _make_builder(tmp_path)
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = builder._cached_read_text(f)
    assert result == ""


# ---------------------------------------------------------------------------
# _get_identity cache
# ---------------------------------------------------------------------------


def test_get_identity_cache_hit(tmp_path):
    """Same channel returns cached identity."""
    builder = _make_builder(tmp_path)
    id1 = builder._get_identity(channel="cli")
    id2 = builder._get_identity(channel="cli")
    assert id1 is id2


def test_get_identity_cache_miss_different_channel(tmp_path):
    """Different channels get different cached entries."""
    builder = _make_builder(tmp_path)
    id_cli = builder._get_identity(channel="cli")
    id_slack = builder._get_identity(channel="slack")
    assert id_cli is not id_slack


def test_get_identity_cache_vector_search_flag(tmp_path):
    """Different include_vector_search values get different cache entries."""
    builder = _make_builder(tmp_path)
    id_with = builder._get_identity(channel="cli", include_vector_search=True)
    id_without = builder._get_identity(channel="cli", include_vector_search=False)
    assert id_with is not id_without


# ---------------------------------------------------------------------------
# _get_memory_info / _get_gpu_info module-level cache
# ---------------------------------------------------------------------------


def test_memory_info_global_cache(tmp_path):
    """_get_memory_info uses module-level cache shared across instances."""
    from nanobot.agent.context import _get_memory_info
    saved = ctx_module._memory_info_cache
    ctx_module._memory_info_cache = ("8.0 GB", "4.0 GB")
    try:
        result = _get_memory_info()
        assert result == ("8.0 GB", "4.0 GB")
    finally:
        ctx_module._memory_info_cache = saved


def test_gpu_info_global_cache(tmp_path):
    """_get_gpu_info uses module-level cache shared across instances."""
    from nanobot.agent.context import _get_gpu_info
    saved = ctx_module._gpu_info_cache
    ctx_module._gpu_info_cache = "RTX 4090"
    try:
        result = _get_gpu_info()
        assert result == "RTX 4090"
    finally:
        ctx_module._gpu_info_cache = saved


# ---------------------------------------------------------------------------
# _build_memory_section caching
# ---------------------------------------------------------------------------


def test_memory_section_cached(tmp_path):
    """Repeated call returns cached result."""
    builder = _make_builder(tmp_path)
    working_file = builder.memory.memory_dir / "working.md"
    working_file.parent.mkdir(parents=True, exist_ok=True)
    working_file.write_text("## Current Task\n\nSome memories.", encoding="utf-8")

    section1 = builder._build_memory_section()
    assert "Some memories" in section1

    section2 = builder._build_memory_section()
    assert section1 == section2


def test_memory_section_invalidated_by_change(tmp_path):
    """Changing working.md invalidates cache."""
    builder = _make_builder(tmp_path)
    working_file = builder.memory.memory_dir / "working.md"
    working_file.parent.mkdir(parents=True, exist_ok=True)
    working_file.write_text("## Current Task\n\nOld.", encoding="utf-8")

    section1 = builder._build_memory_section()
    assert "Old." in section1

    _write_file(working_file, "## Current Task\n\nNew.")

    section2 = builder._build_memory_section()
    assert "New." in section2


# ---------------------------------------------------------------------------
# _build_task_tree_section caching
# ---------------------------------------------------------------------------


def test_task_tree_cached(tmp_path):
    """Repeated call returns cached result."""
    builder = _make_builder(tmp_path)
    tree = tmp_path / "workspace" / "tasks" / "tree.json"
    tree.parent.mkdir(parents=True, exist_ok=True)
    tree.write_text(
        '{"items": [{"id": "1", "name": "Feature X", "status": "active", "doc": "In progress."}]}',
        encoding="utf-8",
    )

    t1 = builder._build_task_tree_section()
    assert "Feature X" in t1

    t2 = builder._build_task_tree_section()
    assert t1 == t2


def test_task_tree_invalidated_by_change(tmp_path):
    """Changing tree.json invalidates cache."""
    builder = _make_builder(tmp_path)
    tree = tmp_path / "workspace" / "tasks" / "tree.json"
    tree.parent.mkdir(parents=True, exist_ok=True)
    tree.write_text(
        '{"items": [{"id": "1", "name": "Old", "status": "active", "doc": "Old description."}]}',
        encoding="utf-8",
    )

    t1 = builder._build_task_tree_section()
    assert "Old" in t1

    _write_file(tree, '{"items": [{"id": "1", "name": "New", "status": "active", "doc": "New description."}]}')

    t2 = builder._build_task_tree_section()
    assert "New" in t2
    assert t1 != t2  # content changed


# ---------------------------------------------------------------------------
# _build_current_context_section caching
# ---------------------------------------------------------------------------


def test_current_context_cached(tmp_path):
    """Repeated call returns cached result."""
    builder = _make_builder(tmp_path)
    current = tmp_path / "workspace" / "tasks" / "CURRENT.md"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("Working on feature X.", encoding="utf-8")

    c1 = builder._build_current_context_section()
    assert "feature X" in c1

    c2 = builder._build_current_context_section()
    assert c1 == c2


# ---------------------------------------------------------------------------
# Integration: build_system_prompt cascade
# ---------------------------------------------------------------------------


def test_build_system_prompt_stable_across_calls(tmp_path):
    """Repeated build_system_prompt with same args returns same result."""
    builder = _make_builder(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "tasks").mkdir(parents=True, exist_ok=True)
    (ws / "tasks" / "tree.json").write_text(
        '{"items": [{"id": "1", "name": "Task One", "status": "active", "doc": "Do it."}]}',
        encoding="utf-8",
    )
    builder._framework_config = {"model": "claude-sonnet-4", "provider": "anthropic"}

    p1 = builder.build_system_prompt(channel="cli")
    p2 = builder.build_system_prompt(channel="cli")
    assert p1 == p2


def test_build_system_prompt_caches_identity_per_channel(tmp_path):
    """Different channels produce different identity cache entries (but same system prompt if template doesn't use channel)."""
    builder = _make_builder(tmp_path)
    builder._framework_config = {"model": "claude-sonnet-4", "provider": "anthropic"}

    id_cli = builder._get_identity(channel="cli")
    id_slack = builder._get_identity(channel="slack")
    # Each channel gets its own cache entry (different objects)
    assert id_cli is not id_slack


# ---------------------------------------------------------------------------
# Scenario: files change mid-session
# ---------------------------------------------------------------------------


def test_scenario_tree_md_changes_mid_session(tmp_path):
    """tree.json changes mid-session, next call to _build_task_tree_section picks it up."""
    builder = _make_builder(tmp_path)
    ws = tmp_path / "workspace"
    tree = ws / "tasks" / "tree.json"
    tree.parent.mkdir(parents=True, exist_ok=True)
    tree.write_text(
        '{"items": [{"id": "1", "name": "Original", "status": "active", "doc": "Original plan."}]}',
        encoding="utf-8",
    )

    t1 = builder._build_task_tree_section()
    assert "Original" in t1

    _write_file(tree, '{"items": [{"id": "1", "name": "Updated", "status": "active", "doc": "Updated plan."}]}')

    t2 = builder._build_task_tree_section()
    assert "Updated" in t2
    assert t1 != t2


def test_scenario_tool_added_mid_session(tmp_path):
    """Tool added mid-session, TOOLS.md regenerated on next build."""
    builder = _make_builder(tmp_path)
    builder._framework_config = {"model": "claude-sonnet-4", "provider": "anthropic"}
    ws = tmp_path / "workspace"

    p1 = builder.build_system_prompt(channel="cli")

    tool_dir = ws / "tools" / "my-helper"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "readme.md").write_text("# My Helper\n\nHelper tool.", encoding="utf-8")
    _rebuild_tools_index(ws)
    # Force distinct mtime for bootstrap cache invalidation on Windows
    tools_md = ws / "TOOLS.md"
    new_mtime = time.time() + 1
    os.utime(tools_md, (new_mtime, new_mtime))

    p2 = builder.build_system_prompt(channel="cli")
    assert p1 != p2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_workspace_no_crash(tmp_path):
    """Empty workspace with no files doesn't crash any section builder."""
    builder = _make_builder(tmp_path)

    assert builder._build_task_tree_section() == ""
    assert builder._build_current_context_section() == ""
    assert builder._build_memory_section() == ""
    bootstrap = builder._load_bootstrap_files()
    assert isinstance(bootstrap, str)


def test_cached_read_text_nonexistent_dir(tmp_path):
    """Non-existent directory path returns None."""
    builder = _make_builder(tmp_path)
    result = builder._cached_read_text(tmp_path / "nosuchdir" / "file.md")
    assert result is None
