"""Tool to list all running background subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class ListSubagentsTool(Tool):
    """Tool to query the status of all running background subagents."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    name = "list_subagents_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: List all running background subagents and their status.\n\n"
            "**When to use**:\n"
            "- Want to know how many subagents are currently running\n"
            "- Forgot a subagent's task_id\n"
            "- Batch-check subagent progress\n\n"
        )

    async def execute(self, **kwargs: Any) -> str:
        statuses = self._manager.list_running_statuses()
        if not statuses:
            return "No subagents currently running."

        lines = [f"Running subagents ({len(statuses)}):"]
        for s in statuses:
            lines.append(f"  [{s.task_id}] {s.label} — phase: {s.phase}, iter: {s.iteration}")
        return "\n".join(lines)
