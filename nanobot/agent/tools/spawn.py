"""Spawn tool — spawn one or more subagents in a single call."""

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


def _read_workspace_file(workspace: Path, filename: str) -> str:
    """Read a file from workspace."""
    try:
        path = workspace / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Failed to read workspace file {} for subagent context", filename)
    return ""


def build_context_block(workspace: Path | None = None, team_context: str | None = None) -> str:
    """Build context block from current messages and files."""
    messages = _current_messages_for_subagent.get() or []
    parts: list[str] = ["## Context from Main Agent"]

    if workspace is not None:
        for filename in ["SOUL.md", "USER.md", "MEMORY.md", "TOOLS.md"]:
            content = _read_workspace_file(workspace, filename)
            if content:
                parts.append(f"=== {(workspace / filename).as_posix()} ===\n{content[:800]}\n===============")
        for rel in ["tasks/TREE.md", "tasks/CURRENT.md", "tasks/team_board.md"]:
            content = _read_workspace_file(workspace, rel)
            if content:
                parts.append(f"=== {(workspace / rel).as_posix()} ===\n{content[:8000]}\n===============")

    recent = messages[-10:] if len(messages) > 10 else messages
    if recent:
        parts.append("### Recent Conversation")
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            text = content[:400] + "..." if len(content) > 400 else content if content else ""
            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc.strip():
                think = rc.strip()[:200] + "..." if len(rc) > 200 else rc.strip()
                text = f"[think]: {think}\n{text}" if text else f"[think]: {think}"
            if text:
                parts.append(f"[{role}]: {text}")

    if team_context:
        parts.append(f"## Team Context\n\n{team_context}")

    return "\n\n".join(parts)


@tool_parameters(
    build_parameters_schema(
        tasks=p("array", "List of tasks to spawn. Each task is an object with fields: task (required), label (optional), role (optional), output_schema (optional), max_iterations (optional).",
            items={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The task for the subagent to complete"},
                    "label": {"type": "string", "description": "Optional short label"},
                    "role": {"type": "string", "description": "Optional expert role specification (e.g. 'Python 安全专家')"},
                    "output_schema": {"type": "string", "description": "Optional JSON output schema"},
                    "max_iterations": {"type": "integer", "description": "Max tool iterations (default 100, max 200)", "default": 100, "maximum": 200},
                },
                "required": ["task"],
            }
        ),
        team_context=p("string", "Optional team context: describe all Subagents, their tasks, and dependencies so each Subagent understands its role."),
        required=["tasks"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn one or more subagents in a single call."""

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

    name = "spawn_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Delegate one or more subtasks to Subagents to run independently in the background without blocking the current conversation.\n\n"
            "You are the Orchestrator; the sub-agent is the Subagent. You are responsible for decomposition, delegation, and composition.\n\n"
            "## ⚠️ Important: Embrace Uncertainty\n\n"
            "spawn is fire-and-forget. You must accept when using it:\n"
            "- **Results arrive asynchronously** — they are not guaranteed to return in the current turn; they may be injected into any subsequent turn\n"
            "- **No ordering guarantee** — multiple tasks may complete in any order\n"
            "- **May interrupt the current topic** — the user may have moved on, and results may suddenly appear\n"
            "- **Tasks may fail** — failed tasks also send a notification; accepting failure is part of spawn's semantics\n\n"
            "If you need **synchronous results**, **sequential execution**, or **zero interruption risk** → **do not use spawn, do it yourself**\n\n"
            "## How It Works\n\n"
            "- spawn returns immediately without blocking the current task\n"
            "- Each subtask runs independently in the background with its own session and context\n"
            "- When a subtask completes, the result is injected into a future conversation turn as a system message\n"
            "- Pass a single-item `tasks` array for one subtask, or multiple items for parallel execution\n"
            "- You can use `check_subagent_tool` to proactively query progress\n"
            "- Use `send_message_tool(recipient='subagent:<label>', ...)` to communicate with running subagents\n\n"
            "## When to Use\n\n"
            "- You have independent, parallel subtasks that do not depend on your intermediate decisions\n"
            "- The subtask involves separate file/search/execution work that benefits from its own context\n"
            "- The subtask may be time-consuming and you don't want the user to wait\n"
            "- **You are willing to embrace uncertainty**\n\n"
            "## Limitations\n\n"
            "- Maximum 200 tool-call iterations per subtask (adjustable via `max_iterations` parameter, default 100)\n"
            "- Can read and execute skills\n"
            "- Cannot nest spawn calls\n"
            "- Cannot use the spawn tool itself\n"
            "- The subtask only has a snapshot of the context at spawn time; it cannot see subsequent conversation turns\n\n"
            "## Result Handling\n\n"
            "- Success → system message notifies you of the result content\n"
            "- Failure → system message notifies you of the error\n"
            "- You can proactively query with `check_subagent_tool(task_id=...)`\n\n"
            "## Examples\n\n"
            'spawn(tasks=[{"task": "Search all files containing TODO", "label": "find-todos"}])\n'
            "→ Searches for TODO in the background; system message notifies you of result\n\n"
            "spawn(tasks=[\n"
            '    {"task": "Analyze module A", "label": "mod-a"},\n'
            '    {"task": "Analyze module B", "label": "mod-b"},\n'
            "    ])\n"
            "→ Multiple tasks start simultaneously; each notifies its result upon completion"
        )

    async def execute(self, tasks: list[dict], team_context: str | None = None, **kwargs: Any) -> str:
        """Spawn multiple subagents."""
        if _in_subagent.get():
            return "Error: subagent cannot spawn sub-subagents."
        # Validate label uniqueness before spawning
        seen_labels: set[str] = set()
        for t in tasks:
            label = t.get("label") or t["task"][:30] + ("..." if len(t["task"]) > 30 else "")
            if label in seen_labels:
                return f"Error: duplicate label '{label}' in spawn tasks. Each task must have a unique label."
            seen_labels.add(label)
        workspace = getattr(self._manager, "workspace", None)
        context = build_context_block(workspace, team_context=team_context)
        results: list[str] = []
        for t in tasks:
            task = t["task"]
            label = t.get("label")
            role = t.get("role")
            output_schema = t.get("output_schema")
            max_iterations = t.get("max_iterations")
            result = await self._manager.spawn(
                task=task,
                label=label,
                role=role,
                output_schema=output_schema,
                context=context,
                origin_channel=self._origin_channel.get(),
                origin_chat_id=self._origin_chat_id.get(),
                session_key=self._session_key.get(),
                max_iterations=max_iterations,
            )
            results.append(result)
        summary = "\n".join(results)
        return f"Spawned {len(tasks)} subagent(s):\n{summary}\n\n请继续按计划推进。"
