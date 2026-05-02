"""Tool call log: query tool execution history for debugging and context."""

from typing import Any

from nanobot.agent.db import NanobotDB
from nanobot.agent.tools.base import Tool, tool_parameters


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "session_key": {
                "type": "string",
                "description": "Filter by session key (session identifier)",
            },
            "tool_name": {
                "type": "string",
                "description": "Filter by tool name (e.g. 'exec', 'read_file', 'grep')",
            },
            "success": {
                "type": "boolean",
                "description": "Filter by success (true=success, false=failed)",
            },
            "min_result_size": {
                "type": "integer",
                "description": "Filter to results larger than N characters",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of records to return (default 20, max 100)",
            },
        },
    }
)
class ToolCallLogTool(Tool):
    """Query tool execution log — who ran what, when, with what result."""

    def __init__(self, db: NanobotDB):
        self._db = db

    @property
    def name(self) -> str:
        return "tool_call_log"

    @property
    def description(self) -> str:
        return (
            "Query tool call execution log.\n\n"
            "Use when:\n"
            "- Debugging a tool that failed\n"
            "- Finding results of a previous tool call\n"
            "- Checking what tools ran in a session\n"
            "- Tracking large results (min_result_size > 5000)\n\n"
            "Parameters:\n"
            "- session_key: Filter by session\n"
            "- tool_name: Filter by tool (e.g. 'exec', 'read_file', 'grep')\n"
            "- success: true=failed only, false=success only\n"
            "- min_result_size: results larger than N chars\n"
            "- limit: max records (default 20)\n\n"
            "Returns: list of tool calls with tool_name, params, result, success, duration_ms, timestamp."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        session_key: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
        min_result_size: int | None = None,
        limit: int = 20,
    ) -> str:
        limit = min(limit, 100)
        rows = self._db.query_tool_calls(
            session_key=session_key,
            tool_name=tool_name,
            success=success,
            min_result_size=min_result_size,
            limit=limit,
        )
        if not rows:
            return "No tool call records found matching the criteria."

        lines = []
        for r in rows:
            status = "✅" if r["success"] else "❌"
            dur = f" {r['duration_ms']}ms" if r.get("duration_ms") else ""
            error = f" [ERROR: {r.get('error', '')}]" if not r["success"] and r.get("error") else ""
            result_preview = (r["result"] or "")[:120].replace("\n", " ")
            if len(r["result"] or "") > 120:
                result_preview += "..."
            lines.append(
                f"{status}[iter {r['iteration']}/turn {r['turn']}] {r['tool_name']}{dur}{error}"
                f"\n  params: {r['params']}"
                f"\n  result: {result_preview}"
                f"\n  {r['timestamp']}"
            )
        return "\n\n".join(lines)
