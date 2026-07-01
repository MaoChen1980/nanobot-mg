"""SendMessageTool — Orchestrator → Subagent messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        recipient=p("string", "Subagent to send to: 'subagent:<label>'. Use list_subagents to get labels."),
        message=p("string", "The message content"),
        required=["recipient", "message"],
    )
)
class SendMessageTool(Tool):
    """Orchestrator → Subagent messaging — send instructions to a running subagent."""

    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager

    instruction = (
        "Send a message to a running Subagent (non-blocking, fire-and-forget). "
        "Use list_subagents to get recipient labels. "
        "The subagent receives the message in its next iteration."
    )

    name = "send_message"

    @property
    def description(self) -> str:
        return (
            "Send a message to a running Subagent (non-blocking, fire-and-forget). "
            "The subagent receives the message and continues executing. "
            "recipient='subagent:<label>' — use list_subagents to get labels."
        )

    async def execute(
        self,
        recipient: str,
        message: str,
        **kwargs: Any,
    ) -> str:
        if not recipient.startswith("subagent:"):
            return f"Error: unknown recipient '{recipient}'. Use 'subagent:<label>'."
        label = recipient[len("subagent:"):]
        if not label:
            return "Error: empty Subagent label. Use 'subagent:<label>'."
        return self._manager.send_to_subagent(subagent_label=label, content=message)
