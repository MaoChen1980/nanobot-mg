"""Tests for tool call logging (db + ToolCallLogTool)."""

from __future__ import annotations

import tempfile, os

import pytest

from nanobot.agent.db import NanobotDB
from nanobot.agent.tools.tool_call_log import ToolCallLogTool


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.gettempdir(), f"nanobot_toolcall_test.db")
    yield p
    if os.path.exists(p):
        os.remove(p)


@pytest.fixture
def db(db_path):
    db = NanobotDB(db_path=db_path)
    yield db
    db.close()


class TestToolCallDB:
    def test_insert_and_query(self, db):
        db.insert_tool_call(
            "s1", iteration=1, turn=1,
            tool_name="read_file_tool",
            params={"path": "a.txt"},
            result="hello world",
            success=True,
        )
        db.insert_tool_call(
            "s1", iteration=1, turn=2,
            tool_name="exec_tool",
            params={"command": "ls"},
            result="Error: boom",
            success=False,
            error="boom",
        )
        db.insert_tool_call(
            "s2", iteration=2, turn=1,
            tool_name="grep_tool",
            params={"path": ".", "pattern": "foo"},
            result="foo bar",
            success=True,
            duration_ms=42,
        )

        # query all
        rows = db.query_tool_calls(limit=10)
        assert len(rows) == 3
        # query by session
        rows_s1 = db.query_tool_calls(session_key="s1", limit=10)
        assert len(rows_s1) == 2
        # query by tool_name
        rows_exec = db.query_tool_calls(tool_name="exec_tool", limit=10)
        assert len(rows_exec) == 1
        assert rows_exec[0]["error"] == "boom"
        # query failures only
        rows_fail = db.query_tool_calls(success=False, limit=10)
        assert len(rows_fail) == 1
        assert rows_fail[0]["tool_name"] == "exec_tool"
        # query min_result_size
        rows_large = db.query_tool_calls(min_result_size=5, limit=10)
        assert all(len(r["result"] or "") >= 5 for r in rows_large)
        # duration_ms
        row_grep = next(r for r in rows if r["tool_name"] == "grep_tool")
        assert row_grep["duration_ms"] == 42

    def test_query_empty(self, db):
        rows = db.query_tool_calls(limit=10)
        assert rows == []


class TestToolCallLogTool:
    @pytest.mark.asyncio
    async def test_no_records(self, db):
        tool = ToolCallLogTool(db=db)
        result = await tool.execute(limit=5)
        assert "No tool call records found" in result

    @pytest.mark.asyncio
    async def test_format_success(self, db):
        db.insert_tool_call(
            "s1", iteration=3, turn=5,
            tool_name="read_file_tool",
            params={"path": "foo.txt"},
            result="file content here",
            success=True,
            duration_ms=17,
        )
        tool = ToolCallLogTool(db=db)
        result = await tool.execute(session_key="s1", limit=5)
        assert "✅" in result
        assert "[iter 3/turn 5] read_file" in result
        assert "17ms" in result
        assert "foo.txt" in result

    @pytest.mark.asyncio
    async def test_format_failure(self, db):
        db.insert_tool_call(
            "s2", iteration=1, turn=2,
            tool_name="exec_tool",
            params={"command": "rm -rf /"},
            result="Error: Permission denied",
            success=False,
            error="Permission denied",
        )
        tool = ToolCallLogTool(db=db)
        result = await tool.execute(session_key="s2", limit=5)
        assert "❌" in result
        assert "[ERROR: Permission denied]" in result