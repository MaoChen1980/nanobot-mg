"""SpawnMany tool — batch spawn multiple subagents in one call."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.context_vars import _in_subagent
from nanobot.agent.tools.spawn import build_context_block

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        tasks=p("array", "List of tasks to spawn. Each task is an object with fields: task (required), label (optional), output_schema (optional), max_iterations (optional).",
            items={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The task for the subagent to complete"},
                    "label": {"type": "string", "description": "Optional short label"},
                    "output_schema": {"type": "string", "description": "Optional JSON output schema"},
                    "max_iterations": {"type": "integer", "description": "Max tool iterations (default 100)"},
                },
                "required": ["task"],
            }
        ),
        team_context=p("string", "Optional team context: describe all Workers, their tasks, and dependencies so each Worker understands its role."),
        required=["tasks"],
    )
)
class SpawnManyTool(Tool):
    """Tool to spawn multiple subagents in a single call."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel: ContextVar[str] = ContextVar("spawn_many_origin_channel", default="cli")
        self._origin_chat_id: ContextVar[str] = ContextVar("spawn_many_origin_chat_id", default="direct")
        self._session_key: ContextVar[str] = ContextVar("spawn_many_session_key", default="cli:direct")

    def set_context(self, channel: str, chat_id: str, effective_key: str | None = None) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel.set(channel)
        self._origin_chat_id.set(chat_id)
        self._session_key.set(effective_key or f"{channel}:{chat_id}")

    name = "spawn_many"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Batch launch multiple subtasks for parallel execution. Starts several independent subtasks in a single call, each running independently in the background.\n\n"
            "## How It Works\n\n"
            "- Accepts an array of tasks; each task is spawned independently\n"
            "- All tasks start simultaneously with no interdependencies\n"
            "- Each task notifies its result asynchronously upon completion\n"
            "- You can check progress with `check_subagent` or `list_subagents`\n\n"
            "## When to Use\n\n"
            "- You have multiple independent, parallel subtasks to process\n"
            "- You need to decompose a large task into multiple independent steps\n"
            "- You need to run the same type of analysis across multiple files/modules simultaneously\n\n"
            "## Limitations\n\n"
            "- Each task follows the same restrictions as `spawn`\n"
            "- Tasks cannot communicate with each other\n"
            "- Result arrival order is non-deterministic\n\n"
            "## Examples\n\n"
            "spawn_many(tasks=[\n"
            '    {"task": "Analyze the structure of module A", "label": "mod-a"},\n'
            '    {"task": "Analyze the structure of module B", "label": "mod-b"},\n'
            '    {"task": "Analyze the structure of module C", "label": "mod-c", "output_schema": \'{"type": "object", "properties": {"classes": {"type": "array"}}}\'},\n'
            "])\n"
            "→ All three subtasks start simultaneously; each notifies its result upon completion"
        )

    async def execute(self, tasks: list[dict], team_context: str | None = None, **kwargs: Any) -> str:
        """Spawn multiple subagents."""
        if _in_subagent.get():
            return "Error: subagent cannot spawn sub-subagents."
        workspace = getattr(self._manager, "workspace", None)
        context = build_context_block(workspace, team_context=team_context)
        results: list[str] = []
        for t in tasks:
            task = t["task"]
            label = t.get("label")
            output_schema = t.get("output_schema")
            max_iterations = t.get("max_iterations")
            result = await self._manager.spawn(
                task=task,
                label=label,
                output_schema=output_schema,
                context=context,
                origin_channel=self._origin_channel.get(),
                origin_chat_id=self._origin_chat_id.get(),
                session_key=self._session_key.get(),
                max_iterations=max_iterations,
            )
            results.append(result)
        summary = "\n".join(results)
        return f"Spawned {len(tasks)} subagents:\n{summary}"
