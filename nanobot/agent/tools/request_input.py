"""RequestOrchestratorInputTool — blocking Worker → Orchestrator question."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        question=p("string", "The question for the Orchestrator"),
        context=p("string", "Optional context to help the Orchestrator understand the question"),
        timeout=p("number", "Seconds to wait before continuing autonomously (default 300)"),
        required=["question"],
    )
)
class RequestOrchestratorInputTool(Tool):
    """Blocking: Worker asks Orchestrator for input, pauses until response or timeout."""

    def __init__(self, manager: "SubagentManager", worker_id: str, worker_label: str) -> None:
        self._manager = manager
        self._worker_id = worker_id
        self._worker_label = worker_label

    name = "request_orchestrator_input"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Request input from the Orchestrator, blocking until a reply is received.\n\n"
            "Use when you need the Orchestrator's decision or information to proceed.\n"
            "Execution pauses after calling, without consuming iteration budget.\n\n"
            "**When to use**:\n"
            "- You need confirmation that your direction is correct\n"
            "- You encounter a decision requiring a global perspective\n"
            "- You need information produced by another Worker\n\n"
            "**Note**: Execution pauses until the Orchestrator replies. "
            "After a timeout (default 300s), execution resumes autonomously."
        )

    async def execute(self, question: str, context: str = "", timeout: float = 300.0, **kwargs: Any) -> str:
        return await self._manager.request_orchestrator_input(
            question=question,
            worker_id=self._worker_id,
            worker_label=self._worker_label,
            context=context,
            timeout=timeout,
        )
