"""RespondToSubagentTool — Orchestrator responds to a Subagent's pending question."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        subagent_id=p("string", "Label of the Subagent to respond to (e.g. 'mod-a-analyzer')"),
        response=p("string", "Your response to the Subagent's question"),
        required=["subagent_id", "response"],
    )
)
class RespondToSubagentTool(Tool):
    """Orchestrator tool: respond to a Subagent's pending request_orchestrator_input."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    name = "respond_to_subagent_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Reply to a Subagent's request, unblocking it.\n\n"
            "After a Subagent calls request_orchestrator_input, use this tool to respond.\n"
            "The Subagent resumes execution upon receiving your reply.\n\n"
            "**When to use**:\n"
            "- A Subagent requests input and needs your decision\n"
            "- A Subagent reports a blocker and you have a solution\n\n"
            "**Notes**:\n"
            "- Use the Subagent's label (not task_id)\n"
            "- If the Subagent has timed out, the reply is ignored\n"
            "- Make responses clear and actionable so the Subagent can proceed directly"
        )

    async def execute(self, subagent_id: str, response: str, **kwargs: Any) -> str:
        return self._manager.respond_to_subagent(subagent_id=subagent_id, response=response)
