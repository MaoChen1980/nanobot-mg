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
    instruction = "Notify the orchestrator about progress, completion, or escalation. Subagent-only tool. Use when you discover a better approach, hit a snag, or need to report progress."

    name = "notify_orchestrator"

    @property
    def description(self) -> str:
        return (
            "Send a notification to the Orchestrator (non-blocking, fire-and-forget). "
            "The Orchestrator will see the message in the next iteration. "
            "Priority: info, suggestion, or blocker. "
            "Execution continues after calling — no reply expected."
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