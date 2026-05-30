"""SendMessageTool — bidirectional Worker ↔ Orchestrator messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        recipient=p("string", "Who to send to: 'main' (subagent→orchestrator) or 'worker:<label>' (orchestrator→worker)"),
        message=p("string", "The message content"),
        priority=p("string", "Priority: info, suggestion, or blocker (only for recipient='main')"),
        required=["recipient", "message"],
    )
)
class SendMessageTool(Tool):
    """Bidirectional messaging between Orchestrator and Workers.

    When called by a Worker (subagent), sends to the Orchestrator ('main').
    When called by the Orchestrator (main agent), sends to a Worker ('worker:<label>').
    """

    def __init__(
        self,
        manager: SubagentManager,
        worker_id: str | None = None,
        worker_label: str | None = None,
    ) -> None:
        self._manager = manager
        self._worker_id = worker_id
        self._worker_label = worker_label

    name = "send_message"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Send a message to the Orchestrator or a Worker (non-blocking).\n\n"
            "- From Worker → Orchestrator: `send_message(recipient='main', message=...)`\n"
            "- From Orchestrator → Worker: `send_message(recipient='worker:<label>', message=...)`\n\n"
            "**Fire-and-forget**: execution continues immediately on both sides.\n"
            "The recipient will see your message in their next iteration.\n\n"
            "**Priority** (Worker→Orchestrator only):\n"
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
        # Worker → Orchestrator
        if recipient == "main":
            if self._worker_id is None or self._worker_label is None:
                return "Error: send_message from 'main' is only available to Workers."
            if priority not in ("info", "suggestion", "blocker"):
                priority = "info"
            return await self._manager.notify_orchestrator(
                message=message,
                worker_id=self._worker_id,
                worker_label=self._worker_label,
                priority=priority,
            )

        # Orchestrator → Worker
        if recipient.startswith("worker:"):
            if self._worker_id is not None:
                return "Error: Workers can only send to 'main'."
            label = recipient[len("worker:"):]
            if not label:
                return "Error: empty worker label. Use 'worker:<label>'."
            return self._manager.send_to_worker(worker_label=label, content=message)

        return f"Error: unknown recipient '{recipient}'. Use 'main' or 'worker:<label>'."
