"""RespondToWorkerTool — Orchestrator responds to a Worker's pending question."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        worker_id=p("string", "Label of the Worker to respond to (e.g. 'mod-a-analyzer')"),
        response=p("string", "Your response to the Worker's question"),
        required=["worker_id", "response"],
    )
)
class RespondToWorkerTool(Tool):
    """Orchestrator tool: respond to a Worker's pending request_orchestrator_input."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    name = "respond_to_worker"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Reply to a Worker's request, unblocking it.\n\n"
            "After a Worker calls request_orchestrator_input, use this tool to respond.\n"
            "The Worker resumes execution upon receiving your reply.\n\n"
            "**When to use**:\n"
            "- A Worker requests input and needs your decision\n"
            "- A Worker reports a blocker and you have a solution\n\n"
            "**Notes**:\n"
            "- Use the Worker's label as worker_id (not task_id)\n"
            "- If the Worker has timed out, the reply is ignored\n"
            "- Make responses clear and actionable so the Worker can proceed directly"
        )

    async def execute(self, worker_id: str, response: str, **kwargs: Any) -> str:
        return self._manager.respond_to_worker(worker_id=worker_id, response=response)
