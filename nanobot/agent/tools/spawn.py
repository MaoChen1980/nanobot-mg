"""Spawn tool for creating background subagents."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.context_vars import _current_messages_for_subagent, _in_subagent

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        task=p("string", "The task for the subagent to complete"),
        label=p("string", "Optional short label for the task (for display)"),
        output_schema=p("string", "Optional JSON schema describing the expected output format. When provided, the sub-agent will be instructed to structure its response accordingly, making it easier for you to parse and compose results from multiple sub-agents."),
        max_iterations=p("integer", "Maximum tool call iterations (default 100)"),
        team_context=p("string", "Optional team context: describe other Workers, their tasks, and dependencies so this Worker understands its role in the team."),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel: ContextVar[str] = ContextVar("spawn_origin_channel", default="cli")
        self._origin_chat_id: ContextVar[str] = ContextVar("spawn_origin_chat_id", default="direct")
        self._session_key: ContextVar[str] = ContextVar("spawn_session_key", default="cli:direct")

    def set_context(self, channel: str, chat_id: str, effective_key: str | None = None) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel.set(channel)
        self._origin_chat_id.set(chat_id)
        self._session_key.set(effective_key or f"{channel}:{chat_id}")

    name = "spawn"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Delegate a subtask to a Specialist Worker to run independently in the background without blocking the current conversation.\n\n"
            "You are the Orchestrator; the sub-agent is the Specialist Worker. You are responsible for decomposition, delegation, and composition.\n\n"
            "## ⚠️ Important: Embrace Uncertainty\n\n"
            "spawn is fire-and-forget. You must accept when using it:\n"
            "- **Results arrive asynchronously** — they are not guaranteed to return in the current turn; they may be injected into any subsequent turn\n"
            "- **No ordering guarantee** — multiple spawns may complete in any order\n"
            "- **May interrupt the current topic** — the user may have moved on, and results may suddenly appear\n"
            "- **Subtasks may fail** — failed spawns also send a notification; accepting failure is part of spawn's semantics\n\n"
            "If you need **synchronous results**, **sequential execution**, or **zero interruption risk** → **do not use spawn, do it yourself**\n\n"
            "## How It Works\n\n"
            "- spawn returns immediately without blocking the current task\n"
            "- The subtask runs independently in the background with its own session and context\n"
            "- When the subtask completes, the result is injected into a future conversation turn as a system message\n"
            "- You can use `check_subagent` to proactively query progress\n\n"
            "## When to Use\n\n"
            "- You have independent, parallel subtasks that do not depend on your intermediate decisions\n"
            "- The subtask involves separate file/search/execution work that benefits from its own context\n"
            "- The subtask may be time-consuming and you don't want the user to wait\n"
            "- **You are willing to embrace uncertainty**\n\n"
            "## Limitations\n\n"
            "- Maximum 100 tool-call iterations per subtask (adjustable via `max_iterations` parameter)\n"
            "- Can read and execute skills\n"
            "- Cannot nest spawn calls\n"
            "- Cannot use the spawn tool itself\n"
            "- The subtask only has a snapshot of the context at spawn time; it cannot see subsequent conversation turns\n\n"
            "## Result Handling\n\n"
            "- Success → system message notifies you of the result content\n"
            "- Failure → system message notifies you of the error\n"
            "- You can proactively query with `check_subagent(task_id=...)`\n\n"
            "## Examples\n\n"
            'spawn(task="Search all files containing TODO", label="find-todos")\n'
            "→ Searches for TODO in the background; system message notifies you when complete\n\n"
            "spawn(\n"
            '    task="Analyze the structure of src/utils.py",\n'
            '    label="utils-analysis",\n'
            '    output_schema=\'{"type": "object", "properties": {"classes": {"type": "array"}, "functions": {"type": "array"}}}\'\n'
            ")\n"
            "→ Analyzes the module in the background; returns structured results matching the schema for easy composition"
        )

    async def execute(self, task: str, label: str | None = None, output_schema: str | None = None, max_iterations: int | None = None, team_context: str | None = None, **kwargs: Any) -> str:
        """Spawn a subagent to execute the given task."""
        if _in_subagent.get():
            return "Error: subagent cannot spawn sub-subagents."
        workspace = getattr(self._manager, "workspace", None)
        context = build_context_block(workspace, team_context=team_context)
        return await self._manager.spawn(
            task=task,
            label=label,
            output_schema=output_schema,
            context=context,
            origin_channel=self._origin_channel.get(),
            origin_chat_id=self._origin_chat_id.get(),
            session_key=self._session_key.get(),
            max_iterations=max_iterations,
        )


def build_context_block(workspace: Path | None = None, team_context: str | None = None) -> str:
    """Build context block from current messages and files."""
    messages = _current_messages_for_subagent.get() or []
    parts: list[str] = ["## Context from Main Agent"]

    if workspace is not None:
        for filename in ["SOUL.md", "USER.md", "MEMORY.md", "TOOLS.md"]:
            content = _read_workspace_file(workspace, filename)
            if content:
                parts.append(f"=== {filename} ===\n{content[:800]}\n===============")

    recent = messages[-10:] if len(messages) > 10 else messages
    if recent:
        parts.append("### Recent Conversation")
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if content:
                if len(content) > 400:
                    content = content[:400] + "..."
                parts.append(f"[{role}]: {content}")

    if team_context:
        parts.append(f"## Team Context\n\n{team_context}")

    return "\n\n".join(parts)


def _read_workspace_file(workspace: Path, filename: str) -> str:
    """Read a file from workspace."""
    try:
        path = workspace / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Failed to read workspace file {} for subagent context", filename)
        pass
    return ""
