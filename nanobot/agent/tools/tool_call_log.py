"""Tool call log: query tool execution history for debugging and context."""

from __future__ import annotations


from nanobot.agent.db import NanobotDB
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        session_key=p("string",
            "Filter by session key (session identifier). "
            "To find the current session's key: check the "
            "session info at the top of your context (usually displayed as "
            "'Session: <key>' in the system prompt). "
            "Without a session_key filter, returns calls from all sessions."
        ),
        tool_name=p("string", "Filter by tool name (e.g. 'exec', 'read_file', 'grep')"),
        success=p("boolean", "Filter by success (true=success, false=failed). Omit to return all."),
        min_result_size=p("integer", "Filter to results larger than N characters"),
        limit=p("integer", "Maximum number of records to return (default 20, max 100)", default=20, maximum=100),
    )
)
class ToolCallLogTool(Tool):
    """Query tool execution log — who ran what, when, with what result."""

    def __init__(self, db: NanobotDB):
        self._db = db
    instruction = "View recent tool call history for debugging and tracing."

    name = "tool_call_log"

    description = (
        "Query tool call execution history from the database. "
        "Filter by session_key, tool_name, success status, and result size. "
        "Returns timestamp, params, result preview, and duration."
    )

    read_only = True

    async def execute(
        self,
        session_key: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
        min_result_size: int | None = None,
        limit: int = 20,
        **kwargs: Any,
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
            status = "[OK]" if r["success"] else "[FAIL]"
            dur = f" {r['duration_ms']}ms" if r.get("duration_ms") else ""
            error = f" [ERROR: {r.get('error', '')}]" if not r["success"] and r.get("error") else ""
            result_preview = (r["result"] or "")[:500].replace("\n", " ")
            if len(r["result"] or "") > 500:
                result_preview += "..."
            lines.append(
                f"{status}[iter {r['iteration']}/turn {r['turn']}] {r['tool_name']}{dur}{error}"
                f"\n  params: {r['params']}"
                f"\n  result: {result_preview}"
                f"\n  {r['timestamp']}"
            )
        return "\n\n".join(lines)