"""SendMessageTool — bidirectional Subagent ↔ Orchestrator messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        recipient=p("string", "Who to send to: 'main' (subagent→orchestrator) or 'subagent:<label>' (orchestrator→subagent)"),
        message=p("string", "The message content"),
        priority=p("string", "Priority: info, suggestion, or blocker (only for recipient='main')"),
        required=["recipient", "message"],
    )
)
class SendMessageTool(Tool):
    """Bidirectional messaging between Orchestrator and Subagents.

    When called by a Subagent (subagent), sends to the Orchestrator ('main').
    When called by the Orchestrator (main agent), sends to a Subagent ('subagent:<label>').
    """

    def __init__(
        self,
        manager: SubagentManager,
        subagent_id: str | None = None,
        subagent_label: str | None = None,
    ) -> None:
        self._manager = manager
        self._subagent_id = subagent_id
        self._subagent_label = subagent_label

    name = "send_message_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Send a message to the Orchestrator or a Subagent (non-blocking).\n\n"
            "- From Subagent → Orchestrator: `send_message(recipient='main', message=...)`\n"
            "- From Orchestrator → Subagent: `send_message(recipient='subagent:<label>', message=...)`\n\n"
            "**Fire-and-forget**: execution continues immediately on both sides.\n"
            "The recipient will see your message in their next iteration.\n\n"
            "**Priority** (Subagent→Orchestrator only):\n"
            "- info: General information, progress reports\n"
            "- suggestion: Improvement suggestions (found a better approach)\n"
            "- blocker: Blocking issue requiring Orchestrator decision\n\n"
            "If you need a reply from the recipient, use request_orchestrator_input instead."
        )

    async def execute(
        self,
        recipient: str,
        message: str,
        priority: str = "info",
        **kwargs: Any,
    ) -> str:
        # Subagent → Orchestrator
        if recipient == "main":
            if self._subagent_id is None or self._subagent_label is None:
                return "Error: send_message from 'main' is only available to Subagents."
            if priority not in ("info", "suggestion", "blocker"):
                priority = "info"
            return await self._manager.notify_orchestrator(
                message=message,
                subagent_id=self._subagent_id,
                subagent_label=self._subagent_label,
                priority=priority,
            )

        # Orchestrator → Subagent
        if recipient.startswith("subagent:"):
            if self._subagent_id is not None:
                return "Error: Subagents can only send to 'main'."
            label = recipient[len("subagent:"):]
            if not label:
                return "Error: empty Subagent label. Use 'subagent:<label>'."
            return self._manager.send_to_subagent(subagent_label=label, content=message)

        return f"Error: unknown recipient '{recipient}'. Use 'main' or 'subagent:<label>'."
