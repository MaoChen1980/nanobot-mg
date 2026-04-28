"""Spawn tool for creating background subagents."""

from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
from nanobot.agent.context_vars import _current_messages_for_subagent

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
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

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return """
Spawn a subagent to handle independent tasks in the background. The subagent will complete the task and report back when done.

## ✅ When to Use
- Search and analyze multiple files in parallel
- Fetch and summarize content from multiple URLs
- Read, write, or edit files
- Run read-only analysis (code analysis, data inspection)
- Execute shell commands or scripts
- Read multiple documents and compile findings
- Quick research or coding tasks that don't affect main context

## ❌ NEVER Use For
- Tasks requiring your intermediate decisions or feedback
- Tasks whose results are needed by subsequent steps in the main agent
- Creating external resources, accounts, or services

## Constraints
- Subagent has its own isolated session (no access to main conversation)
- Subagent cannot spawn further subagents (no nesting)
- Results will be announced as a system message
- Max 30 tool-use iterations per subagent
- Subagent tools: read_file, list_dir, glob, grep, write_file, edit_file, web_search, web_fetch, exec (no spawn, no session_manage)
""".strip()

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        """Spawn a subagent to execute the given task."""
        context = self._build_context_block()
        return await self._manager.spawn(
            task=task,
            label=label,
            context=context,
            origin_channel=self._origin_channel.get(),
            origin_chat_id=self._origin_chat_id.get(),
            session_key=self._session_key.get(),
        )

    def _build_context_block(self) -> str:
        """Build context block from current messages and files."""
        messages = _current_messages_for_subagent.get() or []
        parts: list[str] = ["## Context from Main Agent"]

        # Workspace bootstrap files (only if workspace is available on the manager)
        workspace = getattr(self._manager, "workspace", None)
        if workspace is not None:
            for filename in ["SOUL.md", "USER.md", "MEMORY.md", "AGENTS.md", "TOOLS.md"]:
                content = self._read_file(workspace, filename)
                if content:
                    parts.append(f"=== {filename} ===\n{content[:800]}\n===============")

        # Recent messages (last 10) — preserve structure
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

        return "\n\n".join(parts)

    def _read_file(self, workspace: Path, filename: str) -> str:
        """Read a file from workspace."""
        try:
            path = workspace / filename
            if path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""
