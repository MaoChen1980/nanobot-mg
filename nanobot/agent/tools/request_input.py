"""RequestOrchestratorInputTool — blocking Subagent → Orchestrator question."""

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
    """Blocking: Subagent asks Orchestrator for input, pauses until response or timeout."""

    def __init__(self, manager: "SubagentManager", subagent_id: str, subagent_label: str) -> None:
        self._manager = manager
        self._subagent_id = subagent_id
        self._subagent_label = subagent_label

    name = "request_orchestrator_input_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Request input from the Orchestrator, blocking until a reply is received.\n\n"
            "Use when you need the Orchestrator's decision or information to proceed.\n"
            "Execution pauses after calling, without consuming iteration budget.\n\n"
            "**When to use**:\n"
            "- You need confirmation that your direction is correct\n"
            "- You encounter a decision requiring a global perspective\n"
            "- You need information produced by another Subagent\n\n"
            "**Note**: Execution pauses until the Orchestrator replies. "
            "After a timeout (default 300s), execution resumes autonomously."
        )

    async def execute(self, question: str, context: str = "", timeout: float = 300.0, **kwargs: Any) -> str:
        return await self._manager.request_orchestrator_input(
            question=question,
            subagent_id=self._subagent_id,
            subagent_label=self._subagent_label,
            context=context,
            timeout=timeout,
        )
