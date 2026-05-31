"""Tool to cancel a specific running subagent by its worker label."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        label=p("string", "The worker label of the subagent to cancel"),
        required=["label"],
    )
)
class CancelSubagentTool(Tool):
    """Tool to cancel a specific running subagent by worker label."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    name = "cancel_subagent"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Cancel a specific running subagent by its worker label. "
            "The subagent will be force-stopped and you will receive a cancellation notice.\n\n"
            "**When to use**:\n"
            "- A subagent is no longer needed (task abandoned)\n"
            "- A subagent is stuck and needs to be terminated\n"
            "- You want to reassign resources to a different task\n\n"
            "Use `list_subagents` first to see active worker labels.\n"
        )

    async def execute(self, label: str, **kwargs: Any) -> str:
        return await self._manager.cancel_by_label(label)
