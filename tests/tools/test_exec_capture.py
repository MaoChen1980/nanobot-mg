"""Tests for ExecTool capture_file output and fd cleanup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nanobot.agent.tools.shell import ExecTool


@pytest.mark.asyncio
class TestExecCapture:
    """capture_file parameter writes output and cleans up fds."""

    async def test_capture_file_creates_file(self, tmp_path):
        cap = str(tmp_path / "out.txt")
        tool = ExecTool(working_dir=str(tmp_path))
        await tool.execute(command="echo hello", capture_file=cap)
        assert os.path.exists(cap)
        content = Path(cap).read_text(encoding="utf-8")
        assert "hello" in content

    async def test_capture_file_deletable_after_exec(self, tmp_path):
        """No leaked fd — file can be deleted on all platforms."""
        cap = str(tmp_path / "out.txt")
        tool = ExecTool(working_dir=str(tmp_path))
        await tool.execute(command="echo hello", capture_file=cap)
        Path(cap).unlink()
        assert not os.path.exists(cap)

    async def test_capture_file_contains_multiple_lines(self, tmp_path):
        cap = str(tmp_path / "out.txt")
        tool = ExecTool(working_dir=str(tmp_path))
        await tool.execute(command="echo hello; echo world", capture_file=cap)
        content = Path(cap).read_text(encoding="utf-8")
        assert "hello" in content
        assert "world" in content
