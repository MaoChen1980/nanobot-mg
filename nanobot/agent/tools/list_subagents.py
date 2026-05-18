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

    name = "list_subagents"

    @property
    def description(self) -> str:
        return (
            "**用途**: 列出所有正在运行的后台子任务及其状态。\n\n"
            "**什么时候用**:\n"
            "- 想知道当前有多少子任务在跑\n"
            "- 忘记某个子任务的 task_id\n"
            "- 批量检查子任务进度\n\n"
            "**什么时候不用**:\n"
            "- 需要查询单个子任务的详细状态 → 用 check_subagent"
        )

    async def execute(self, **kwargs: Any) -> str:
        statuses = self._manager.list_running_statuses()
        if not statuses:
            return "No subagents currently running."

        lines = [f"Running subagents ({len(statuses)}):"]
        for s in statuses:
            lines.append(f"  [{s.task_id}] {s.label} — phase: {s.phase}, iter: {s.iteration}")
        return "\n".join(lines)
