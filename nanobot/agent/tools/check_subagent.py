"""Tool to check the status of a running subagent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        task_id=p("string", "The subagent task ID to check (from spawn return value)"),
        required=["task_id"],
    )
)
class CheckSubagentTool(Tool):
    """Tool to query the execution status of a background subagent."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager
    instruction = "Check a specific subagent's status. Use list_subagents first to get the task_id."

    name = "check_subagent"

    @property
    def description(self) -> str:
        return (
            "Query a subagent's execution status: phase, iteration count, "
            "tools executed, token usage, and any error. "
            "Use list_subagents first to get the task_id."
            "\n\nOutput example:\n"
            "  Subagent [explore] status:\n"
            "    Phase: completed\n"
            "    Iteration: 12\n"
            "    Tools executed: read_file, grep\n"
            "    Token usage: 500 prompt / 1000 completion"
        )

    async def execute(self, task_id: str, **kwargs: Any) -> str:
        status = self._manager.get_status(task_id)
        if status is None:
            return f"Subagent '{task_id}' not found (already completed or never existed)."

        lines = [f"Subagent [{status.label}] status:"]
        lines.append(f"  Phase: {status.phase}")
        lines.append(f"  Iteration: {status.iteration}")
        if status.tools_ran:
            lines.append(f"  Tools executed: {', '.join(status.tools_ran)}")
        if status.usage:
            usage = status.usage
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            lines.append(f"  Token usage: {prompt} prompt / {completion} completion")
        if status.error:
            lines.append(f"  Error: {status.error}")
        if status.stop_reason:
            lines.append(f"  Stop reason: {status.stop_reason}")
        return "\n".join(lines)