"""Tests for exec tool caching, grep, extract, and from_cache features."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.tools.shell import ExecTool


# ---------------------------------------------------------------------------
# Cache saving
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_saves_to_cache(tmp_path):
    """After execution, output is saved to the cache directory."""
    tool = ExecTool(working_dir=str(tmp_path))
    with patch.object(ExecTool, "_guard_command", return_value=None):
        # Mock _spawn to return a controlled result
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"hello world\nline 2\nline 3\n", b"")
        mock_proc.returncode = 0
        with patch.object(ExecTool, "_spawn", return_value=mock_proc):
            result = await tool.execute(command="echo hello")

    assert "Full output cached:" in result

    # Verify a cache file was created in the exec_cache dir
    from nanobot.config.paths import get_runtime_subdir
    cache_dir = get_runtime_subdir("exec_cache")
    json_files = list(cache_dir.glob("*.json"))
    assert len(json_files) >= 1

    # Verify cache content
    latest = max(json_files, key=lambda f: f.stat().st_mtime)
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert data["command"] == "echo hello"
    assert data["exit_code"] == 0
    assert "hello world" in data["stdout"]


# ---------------------------------------------------------------------------
# Default return format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_return_includes_cache_path(tmp_path):
    """Default exec result includes the cache file path."""
    tool = ExecTool(working_dir=str(tmp_path))
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"ok", b"")
    mock_proc.returncode = 0
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute(command="echo ok")

    assert "[Full output cached:" in result


# ---------------------------------------------------------------------------
# grep mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_filters_output(tmp_path):
    """grep parameter returns only lines matching the pattern."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (
        b"line 1: apple\nline 2: banana\nline 3: apple pie\nline 4: cherry\n",
        b"",
    )
    mock_proc.returncode = 0
    tool = ExecTool(working_dir=str(tmp_path))
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute(command="echo test", grep="apple")

    assert "apple" in result
    assert "banana" not in result
    assert "cherry" not in result
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_grep_returns_exit_code(tmp_path):
    """grep result still shows the exit code."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"hello\nworld\n", b"")
    mock_proc.returncode = 1
    tool = ExecTool(working_dir=str(tmp_path))
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute(command="false", grep="hello")

    assert "Exit code: 1" in result


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path):
    """When no lines match, grep returns a clear message."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"aaa\nbbb\nccc\n", b"")
    mock_proc.returncode = 0
    tool = ExecTool(working_dir=str(tmp_path))
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute(command="echo test", grep="zzz")

    assert "No lines matched" in result


# ---------------------------------------------------------------------------
# extract mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_with_cache_placeholder(tmp_path):
    """{cache} in extract is replaced with the actual file path."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"some output\n" * 10, b"")
    mock_proc.returncode = 0

    # Mock the second spawn (for the extract command)
    mock_extract_proc = AsyncMock()
    mock_extract_proc.communicate.return_value = (b"10\n", b"")
    mock_extract_proc.returncode = 0

    spawns = [mock_proc, mock_extract_proc]
    spawn_index = 0

    async def side_effect_spawn(*args, **kwargs):
        nonlocal spawn_index
        proc = spawns[spawn_index]
        spawn_index += 1
        return proc

    tool = ExecTool(working_dir=str(tmp_path))
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", side_effect=side_effect_spawn),
        patch.object(ExecTool, "_build_env", return_value={}),
    ):
        result = await tool.execute(
            command="echo test",
            extract="python -c \"import sys; data=open('{cache}').read(); print(len(data.splitlines()))\"",
        )

    assert "[Extract result]:" in result
    assert "10" in result


# ---------------------------------------------------------------------------
# from_cache mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_cache_skips_execution():
    """from_cache loads previous output without executing."""
    from nanobot.config.paths import get_runtime_subdir
    cache_dir = get_runtime_subdir("exec_cache")
    cache_path = cache_dir / "test_cache_0001.json"
    cache_data = {
        "command": "echo cached",
        "exit_code": 42,
        "stdout": "cached output line 1\ncached output line 2\n",
        "stderr": "",
        "timestamp": int(time.time()),
    }
    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

    # Now use from_cache — no _spawn should happen
    tool = ExecTool()
    with patch.object(ExecTool, "_spawn") as mock_spawn:
        result = await tool.execute(from_cache=str(cache_path))

    assert mock_spawn.call_count == 0  # No execution
    assert "Loaded from cache:" in result
    assert "Exit: 42" in result
    assert "cached output" in result


@pytest.mark.asyncio
async def test_from_cache_with_grep():
    """from_cache combined with grep filters cached output."""
    from nanobot.config.paths import get_runtime_subdir
    cache_dir = get_runtime_subdir("exec_cache")
    cache_path = cache_dir / "test_cache_grep.json"
    cache_data = {
        "command": "test",
        "exit_code": 1,
        "stdout": "INFO: start\nERROR: crashed\nINFO: cleanup\n",
        "stderr": "",
        "timestamp": int(time.time()),
    }
    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

    tool = ExecTool()
    with patch.object(ExecTool, "_spawn") as mock_spawn:
        result = await tool.execute(from_cache=str(cache_path), grep="ERROR")

    assert mock_spawn.call_count == 0
    assert "ERROR: crashed" in result
    assert "INFO" not in result
    assert "Exit code: 1" in result


@pytest.mark.asyncio
async def test_from_cache_file_not_found():
    """from_cache with a non-existent path returns a clear error."""
    tool = ExecTool()
    result = await tool.execute(from_cache="/nonexistent/cache.json")
    assert "Error" in result
    assert "not found" in result


# ---------------------------------------------------------------------------
# Compatibility: existing behavior preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_allows_normal_commands(tmp_path):
    """Basic exec still works as before (backward compat)."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(command="echo hello")
    assert "hello" in result
    assert "Error" not in result.split("\n")[0]


@pytest.mark.asyncio
async def test_exec_exit_code_preserved(tmp_path):
    """Exit code is still shown in the result."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    tool = ExecTool(working_dir=str(tmp_path))
    with (
        patch.object(ExecTool, "_guard_command", return_value=None),
        patch.object(ExecTool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute(command="true")
    assert "Exit: 0" in result


@pytest.mark.asyncio
async def test_from_cache_no_command_required():
    """from_cache does not require the command parameter."""
    tool = ExecTool()
    result = await tool.execute(from_cache="/nonexistent/cache.json")
    assert "not found" in result


@pytest.mark.asyncio
async def test_exec_rejects_empty_command():
    """Without command and without from_cache, returns error."""
    tool = ExecTool()
    result = await tool.execute()
    assert "Error" in result
    assert "command is required" in result
