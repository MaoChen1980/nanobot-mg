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


def build_context_block(workspace: Path | None = None, team_context: str | None = None, session_key: str | None = None) -> str:
    """Build context block from current messages and files."""
    from nanobot.agent.context import _sanitize_session_key

    messages = _current_messages_for_subagent.get() or []
    parts: list[str] = ["## Context from Main Agent"]

    if workspace is not None:
        for filename in ["SOUL.md", "USER.md", "MEMORY.md", "TOOLS.md"]:
            content = _read_workspace_file(workspace, filename)
            if content:
                parts.append(f"=== {(workspace / filename).as_posix()} ===\n{content[:800]}\n===============")
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        for rel in [f"tasks/tree{suffix}.json", f"tasks/CURRENT{suffix}.md", f"tasks/team_board{suffix}.md"]:
            content = _read_workspace_file(workspace, rel)
            if content:
                parts.append(f"=== {(workspace / rel).as_posix()} ===\n{content[:8000]}\n===============")

    user_msgs = [m for m in messages if m.get("role") != "system"]
    recent = user_msgs[-10:] if len(user_msgs) > 10 else user_msgs
    if recent:
        parts.append("### 近期对话")
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "") or ""
            if not content.strip():
                continue
            if role == "user":
                text = content[:2000] + "..." if len(content) > 2000 else content
                parts.append(f"**用户原话**: {text}")
            else:
                text = content[:600] + "..." if len(content) > 600 else content
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
                    "max_timeout": {"type": "integer", "description": "Optional wall-clock timeout in seconds (default 3600, max 7200). Subagent is killed if it exceeds this limit.", "default": 3600, "minimum": 1},
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
    instruction = (
        "Dispatch parallel independent tasks via fire-and-forget subagents. "
        "Use for: independent parallel subtasks, time-consuming work that benefits from its own context. "
        "Do NOT use when you need synchronous results, sequential execution, or zero interruption risk. "
        "After spawning: "
        "1) If spawn covered ALL remaining work → stop tool_calls. "
        "Results arrive automatically as user messages — do NOT poll with check_subagent+exec(sleep). "
        "2) If you still have independent non-delegated work → do it in parallel. "
        "3) When a subagent result arrives → integrate it. If partial, spawn another for the missing scope."
    )

    name = "spawn"

    @property
    def description(self) -> str:
        return (
            "Delegate one or more subtasks to Subagents to run independently in the background "
            "without blocking the current conversation. "
            "Fire-and-forget — results arrive asynchronously, no ordering guarantee. "
            "Max 200 tool-call iterations per subtask (adjustable via max_iterations). "
            "Wall-clock timeout in seconds (adjustable via max_timeout — subagent killed on expiry). "
            "Cannot nest spawn calls. Each subtask gets a snapshot of context at spawn time. "
            "Use check_subagent to query progress, send_message to communicate with running subagents."
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
        context = build_context_block(workspace, team_context=team_context, session_key=self._session_key.get())
        results: list[str] = []
        for t in tasks:
            task = t["task"]
            label = t.get("label")
            role = t.get("role")
            output_schema = t.get("output_schema")
            max_iterations = t.get("max_iterations", 100)
            max_timeout = t.get("max_timeout")
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
                max_timeout=max_timeout,
            )
            results.append(result)
        summary = "\n".join(results)
        return f"Spawned {len(tasks)} subagent(s):\n{summary}"