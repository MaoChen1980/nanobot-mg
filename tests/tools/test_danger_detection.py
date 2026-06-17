"""Tests for danger detection module and danger_override mechanism."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.agent.tools.danger import check_overwrite_danger, danger_warning
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.shell_validators import check_command_safety


# ---------------------------------------------------------------------------
# danger_warning formatting
# ---------------------------------------------------------------------------

def test_danger_warning_format():
    result = danger_warning(
        problem="test problem",
        risk="test risk",
        suggestion="test suggestion",
        tool_name="test_tool",
    )
    assert result.startswith("⚠️ Danger:")
    assert "test problem" in result
    assert "test risk" in result
    assert "test suggestion" in result
    assert "danger_override=true" in result
    assert "test_tool" in result


def test_danger_warning_minimal():
    """Without suggestion, the message should still be complete."""
    result = danger_warning(problem="test problem", risk="test risk")
    assert result.startswith("⚠️ Danger:")
    assert "test problem" in result
    assert "test risk" in result
    assert "danger_override=true" in result


# ---------------------------------------------------------------------------
# check_overwrite_danger
# ---------------------------------------------------------------------------

def test_overwrite_danger_new_file(tmp_path):
    """File does not exist — no danger."""
    fp = tmp_path / "new.txt"
    danger, reason = check_overwrite_danger(fp, was_read=False, size_bytes=0)
    assert not danger
    assert reason == ""


def test_overwrite_danger_read_file(tmp_path):
    """File exists but was read — no danger."""
    fp = tmp_path / "read.txt"
    fp.write_text("data")
    danger, reason = check_overwrite_danger(fp, was_read=True, size_bytes=5)
    assert not danger


def test_overwrite_danger_small_file(tmp_path):
    """File exists, not read, but small — no danger (<=1 KB)."""
    fp = tmp_path / "small.txt"
    fp.write_text("x" * 512)
    danger, reason = check_overwrite_danger(fp, was_read=False, size_bytes=512)
    assert not danger


def test_overwrite_danger_large_unread(tmp_path):
    """File exists, not read, >1 KB — danger detected."""
    fp = tmp_path / "large.txt"
    fp.write_text("x" * 2048)
    danger, reason = check_overwrite_danger(fp, was_read=False, size_bytes=2048)
    assert danger
    assert "overwriting" in reason
    assert "without reading" in reason
    assert "large.txt" in reason


# ---------------------------------------------------------------------------
# check_command_safety — dangerous pattern detection
# ---------------------------------------------------------------------------

def test_safety_allows_normal_command():
    """A normal echo command should pass through safely."""
    result = check_command_safety(
        command="echo hello",
        cwd="/tmp",
        deny_patterns=[r"\brm\s+-rf\b"],
        allow_patterns=[],
        restrict_to_workspace=False,
        workspace_root=None,
    )
    assert result is None


def test_safety_returns_warning_for_dangerous_pattern():
    """A dangerous command should return a warning (not Error)."""
    result = check_command_safety(
        command="rm -rf /tmp/foo",
        cwd="/tmp",
        deny_patterns=[r"\brm\s+-rf\b"],
        allow_patterns=[],
        restrict_to_workspace=False,
        workspace_root=None,
    )
    assert result is not None
    assert result.startswith("⚠️ Danger:")
    assert "dangerous pattern" in result.lower()
    assert "danger_override" in result


def test_safety_danger_override_bypasses():
    """With danger_override=true, dangerous commands are allowed."""
    result = check_command_safety(
        command="rm -rf /tmp/foo",
        cwd="/tmp",
        deny_patterns=[r"\brm\s+-rf\b"],
        allow_patterns=[],
        restrict_to_workspace=False,
        workspace_root=None,
        danger_override=True,
    )
    assert result is None


def test_safety_warning_not_error():
    """Warning should NOT start with 'Error' — framework should not treat it as error."""
    result = check_command_safety(
        command="rm -rf /tmp/foo",
        cwd="/tmp",
        deny_patterns=[r"\brm\s+-rf\b"],
        allow_patterns=[],
        restrict_to_workspace=False,
        workspace_root=None,
    )
    assert result is not None
    assert not result.startswith("Error")


# ---------------------------------------------------------------------------
# ExecTool integration — danger_override parameter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exec_tool_dangerous_command_returns_warning(tmp_path):
    """exec_tool should return ⚠️ Danger: for dangerous commands (not Error)."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(command="git push --force")
    assert result.startswith("⚠️ Danger:")
    assert "danger_override" in result


@pytest.mark.asyncio
async def test_exec_tool_danger_override_bypasses(tmp_path):
    """With danger_override=true, exec_tool should execute the command."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(command="echo danger_override_works", danger_override=True)
    assert "danger_override_works" in result


@pytest.mark.asyncio
async def test_exec_tool_safe_command_no_warning(tmp_path):
    """Normal commands should not trigger danger warnings."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(command="echo safe")
    assert "⚠️ Danger:" not in result
    assert "safe" in result


@pytest.mark.asyncio
async def test_exec_tool_danger_override_not_persistent(tmp_path):
    """After a danger_override call, next call should re-enable detection."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    # First call with override
    result1 = await tool.execute(command="echo first", danger_override=True)
    assert "first" in result1
    # Second call without override should still have detection
    result2 = await tool.execute(command="rm -rf /tmp/foo")
    assert result2.startswith("⚠️ Danger:")


# ---------------------------------------------------------------------------
# Registry — ⚠️ Danger: prefix handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_danger_warning_not_error(tmp_path):
    """A tool return starting with ⚠️ Danger: should not be treated as error."""
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.base import Tool

    class DangerTool(Tool):
        name = "danger_test"
        description = "test"
        read_only = False

        @property
        def parameters(self):
            return {
                "type": "object",
                "properties": {},
            }

        async def execute(self, **kwargs):
            return "⚠️ Danger: test warning\n  Risk: test risk\n  To proceed anyway, re-call with danger_override=true"

    registry = ToolRegistry()
    registry.register(DangerTool())
    result = await registry.execute("danger_test", {})
    assert result.startswith("⚠️ Danger:")
    assert "❌" not in result


@pytest.mark.asyncio
async def test_registry_error_still_gets_x(tmp_path):
    """Actual errors still get ❌ appended."""
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.base import Tool

    class ErrorTool(Tool):
        name = "error_test"
        description = "test"
        read_only = False

        @property
        def parameters(self):
            return {
                "type": "object",
                "properties": {},
            }

        async def execute(self, **kwargs):
            return "Error: something went wrong"

    registry = ToolRegistry()
    registry.register(ErrorTool())
    result = await registry.execute("error_test", {})
    assert "❌" in result


# ---------------------------------------------------------------------------
# WriteFileTool — overwrite danger detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_file_overwrite_warning(tmp_path):
    """WriteFileTool should warn when overwriting large unread file."""
    from nanobot.agent.tools.filesystem import WriteFileTool

    existing = tmp_path / "existing.txt"
    existing.write_text("x" * 2048)

    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(existing),
        content="new content",
    )
    assert result.startswith("⚠️ Danger:")
    assert "overwriting" in result.lower()
    assert "danger_override" in result


@pytest.mark.asyncio
async def test_write_file_new_file_no_warning(tmp_path):
    """WriteFileTool should not warn when writing to a new file."""
    from nanobot.agent.tools.filesystem import WriteFileTool

    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(tmp_path / "new.txt"),
        content="new content",
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully wrote" in result


@pytest.mark.asyncio
async def test_write_file_danger_override_bypasses(tmp_path):
    """WriteFileTool should write when danger_override=true."""
    from nanobot.agent.tools.filesystem import WriteFileTool

    existing = tmp_path / "existing.txt"
    existing.write_text("x" * 2048)

    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(existing),
        content="new content",
        danger_override=True,
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully wrote" in result


# ---------------------------------------------------------------------------
# DeleteFileTool — deletion danger detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_file_warning(tmp_path):
    """DeleteFileTool should warn before deleting a file."""
    from nanobot.agent.tools.filesystem import DeleteFileTool

    fp = tmp_path / "to_delete.txt"
    fp.write_text("data")

    tool = DeleteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(path=str(fp))
    assert result.startswith("⚠️ Danger:")
    assert "delete" in result.lower()
    assert "danger_override" in result
    # File should still exist (not deleted)
    assert fp.exists()


@pytest.mark.asyncio
async def test_delete_file_danger_override(tmp_path):
    """DeleteFileTool should delete when danger_override=true."""
    from nanobot.agent.tools.filesystem import DeleteFileTool

    fp = tmp_path / "to_delete.txt"
    fp.write_text("data")

    tool = DeleteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(path=str(fp), danger_override=True)
    assert "⚠️ Danger:" not in result
    assert "Deleted:" in result
    # File should be gone
    assert not fp.exists()


# ---------------------------------------------------------------------------
# EditFileTool — large content removal danger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_file_large_removal_warning(tmp_path):
    """EditFileTool should warn when removing >200 characters."""
    from nanobot.agent.tools.filesystem import EditFileTool

    fp = tmp_path / "edit.txt"
    fp.write_text("x" * 500)

    # Read it first to satisfy file_state
    from nanobot.agent.tools import file_state
    file_state.record_read(fp)

    tool = EditFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(fp),
        old_text="x" * 300,
        new_text="",
    )
    assert result.startswith("⚠️ Danger:")
    assert "removing" in result.lower()

    # File should remain unchanged
    assert fp.read_text() == "x" * 500


@pytest.mark.asyncio
async def test_edit_file_large_removal_override(tmp_path):
    """EditFileTool should proceed when danger_override=true."""
    from nanobot.agent.tools.filesystem import EditFileTool

    fp = tmp_path / "edit.txt"
    orig = "hello " + "x" * 300 + " world"
    fp.write_text(orig)

    # Read it first to satisfy file_state
    from nanobot.agent.tools import file_state
    file_state.record_read(fp)

    tool = EditFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(fp),
        old_text="x" * 300,
        new_text="",
        danger_override=True,
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully edited" in result
    # Content should have been modified
    assert fp.read_text() == "hello  world"


@pytest.mark.asyncio
async def test_edit_file_small_removal_no_warning(tmp_path):
    """EditFileTool should not warn when removing <=200 characters."""
    from nanobot.agent.tools.filesystem import EditFileTool

    fp = tmp_path / "edit.txt"
    fp.write_text("hello world")

    # Read it first to satisfy file_state
    from nanobot.agent.tools import file_state
    file_state.record_read(fp)

    tool = EditFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(fp),
        old_text="hello",
        new_text="hi",
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully edited" in result


# ---------------------------------------------------------------------------
# WriteFileTool — read-then-write scenario tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_file_overwrite_after_read(tmp_path):
    """Writing after reading should NOT trigger overwrite warning."""
    from nanobot.agent.tools.filesystem import WriteFileTool
    from nanobot.agent.tools.filesystem import ReadFileTool

    fp = tmp_path / "existing.txt"
    fp.write_text("x" * 2048)

    # Read the file first
    read_tool = ReadFileTool(allowed_dir=tmp_path)
    await read_tool.execute(path=str(fp))

    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(fp),
        content="new content",
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully wrote" in result


@pytest.mark.asyncio
async def test_write_file_small_overwrite_no_warning(tmp_path):
    """Overwriting a small file (<=1KB, unread) should NOT trigger warning."""
    from nanobot.agent.tools.filesystem import WriteFileTool

    fp = tmp_path / "small.txt"
    fp.write_text("x" * 512)

    tool = WriteFileTool(allowed_dir=tmp_path)
    result = await tool.execute(
        path=str(fp),
        content="new content",
    )
    assert "⚠️ Danger:" not in result
    assert "Successfully wrote" in result


# ---------------------------------------------------------------------------
# MoveFileTool — destination overwrite danger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_move_file_destination_exists_warning(tmp_path):
    """MoveFileTool should warn when destination already exists."""
    from nanobot.agent.tools.filesystem import MoveFileTool

    src = tmp_path / "source.txt"
    src.write_text("source data")
    dst = tmp_path / "dest.txt"
    dst.write_text("dest data")

    tool = MoveFileTool(allowed_dir=tmp_path)
    result = await tool.execute(source=str(src), dest=str(dst))
    assert result.startswith("⚠️ Danger:")
    assert "destination" in result.lower() or "exists" in result.lower()
    # Source should still exist
    assert src.exists()


@pytest.mark.asyncio
async def test_move_file_destination_exists_override(tmp_path):
    """MoveFileTool should move when destination exists + danger_override=true."""
    from nanobot.agent.tools.filesystem import MoveFileTool

    src = tmp_path / "source.txt"
    src.write_text("source data")
    dst = tmp_path / "dest.txt"
    dst.write_text("dest data")

    tool = MoveFileTool(allowed_dir=tmp_path)
    result = await tool.execute(source=str(src), dest=str(dst), danger_override=True)
    assert "⚠️ Danger:" not in result
    assert "Moved:" in result
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "source data"


@pytest.mark.asyncio
async def test_move_file_new_destination_no_warning(tmp_path):
    """MoveFileTool should not warn when destination doesn't exist."""
    from nanobot.agent.tools.filesystem import MoveFileTool

    src = tmp_path / "source.txt"
    src.write_text("data")
    dst = tmp_path / "new_dest.txt"

    tool = MoveFileTool(allowed_dir=tmp_path)
    result = await tool.execute(source=str(src), dest=str(dst))
    assert "⚠️ Danger:" not in result
    assert "Moved:" in result


