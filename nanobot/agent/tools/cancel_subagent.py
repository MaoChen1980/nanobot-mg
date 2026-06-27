"""Tool to cancel a specific running subagent by its subagent label."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        label=p("string", "The subagent label of the subagent to cancel"),
        required=["label"],
    )
)
class CancelSubagentTool(Tool):
    """Tool to cancel a specific running subagent by subagent label."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager
    instruction = "Cancel a running subagent. Use list_subagents first to get the label."

    name = "cancel_subagent"

    @property
    def description(self) -> str:
        return (
            "Cancel and force-stop a running subagent by its label. "
            "Use list_subagents first to get the label."
        )

    async def execute(self, label: str, **kwargs: Any) -> str:
        return await self._manager.cancel_by_label(label)