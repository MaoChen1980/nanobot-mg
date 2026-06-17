"""NotifyOrchestratorTool — fire-and-forget Subagent → Orchestrator notification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        message=p("string", "The message to send to the Orchestrator"),
        priority=p("string", "Priority: info, suggestion, or blocker",
            enum=["info", "suggestion", "blocker"],
        ),
        required=["message"],
    )
)
class NotifyOrchestratorTool(Tool):
    """Fire-and-forget: send a notification from Subagent to Orchestrator."""

    def __init__(self, manager: "SubagentManager", subagent_id: str, subagent_label: str) -> None:
        self._manager = manager
        self._subagent_id = subagent_id
        self._subagent_label = subagent_label

    name = "notify_orchestrator_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Send a notification to the Orchestrator (non-blocking).\n\n"
            "Use when you discover a better approach, hit a snag, or need to report progress.\n"
            "The Orchestrator will see your message in the next iteration.\n\n"
            "**Priority**:\n"
            "- info: General information, progress reports\n"
            "- suggestion: Improvement suggestions (found a better approach)\n"
            "- blocker: Blocking issue requiring Orchestrator decision\n\n"
            "**Note**: This is fire-and-forget; execution continues after calling.\n"
            "If you need a reply from the Orchestrator, the Orchestrator will re-spawn you."
        )

    async def execute(self, message: str, priority: str = "info", **kwargs: Any) -> str:
        if priority not in ("info", "suggestion", "blocker"):
            priority = "info"
        return await self._manager.notify_orchestrator(
            message=message,
            subagent_id=self._subagent_id,
            subagent_label=self._subagent_label,
            priority=priority,
        )
