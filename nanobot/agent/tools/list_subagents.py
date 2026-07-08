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
    instruction = "List all running subagents and their status. Use before check/cancel/tell_subagent to get task_id or label."

    name = "list_subagents"

    @property
    def description(self) -> str:
        return (
            "List all active subagents with their task_id, label, phase, "
            "and iteration count."
            "\n\nOutput example:\n"
            "  Running subagents (2):\n"
            "    [task_abc] explore — phase: executing, iter: 5\n"
            "    [task_def] analyze — phase: completed, iter: 12"
        )

    async def execute(self, **kwargs: Any) -> str:
        statuses = self._manager.list_running_statuses()
        if not statuses:
            return "No subagents currently running."

        lines = [f"Running subagents ({len(statuses)}):"]
        for s in statuses:
            lines.append(f"  [{s.task_id}] {s.label} — phase: {s.phase}, iter: {s.iteration}")
        return "\n".join(lines)