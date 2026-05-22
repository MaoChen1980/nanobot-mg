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
            "**用途**: 委托 Specialist Worker 在后台独立执行子任务，不阻塞当前对话。\n\n"
            "你是 Orchestrator，sub-agent 是 Specialist Worker。由你负责分解、委托、组合。\n\n"
            "## ⚠️ 重要：接受不确定性\n\n"
            "spawn 是 fire-and-forget 模式。发起时必须接受：\n"
            "- **结果异步到达** — 不保证在当前对话的这个 turn 回来，可能在后续任意 turn 注入\n"
            "- **不保证顺序** — 多个 spawn 的完成顺序不确定\n"
            "- **可能打断当前话题** — 用户已经聊到别的事，结果突然插入\n"
            "- **子任务可能失败** — 失败的 spawn 同样会通知，接受失败是 spawn 的正常语义\n\n"
            "如果你需要**同步结果**、需要**顺序执行**、需要**零打断风险** → **不要用 spawn，自己做**\n\n"
            "## 工作机制\n\n"
            "- spawn 立即返回，不阻塞当前任务\n"
            "- 子任务在后台独立运行，有独立的 session 和上下文\n"
            "- 子任务完成后，结果以系统消息注入到后续对话中\n"
            "- 可以用 check_subagent 主动查询进度\n\n"
            "## 什么时候用\n\n"
            "- 有独立、可并行的子任务需要处理，且不依赖你的中间决策\n"
            "- 子任务涉及单独的文件/搜索/执行工作，用独立上下文更清晰\n"
            "- 子任务可能耗时较长，你不想让用户干等\n"
            "- **愿意接受不确定性**\n\n"
            "## 什么时候不用\n\n"
            "- 后续步骤依赖子任务的结果 → 直接自己做，不要 spawn\n"
            "- 需要你的中间决策 → 子任务无法咨询你\n"
            "- 不能接受结果异步到达 → 自己做\n"
            "- 只是简单的文件读/写 → 自己做就行，spawn 有额外开销\n\n"
            "## 限制\n\n"
            "- 子任务最多 100 次工具调用迭代（可通过 max_iterations 参数调整）\n"
            "- 可以阅读和执行 skills\n"
            "- 不能嵌套 spawn\n"
            "- 不可用 spawn 工具自身\n"
            "- 子任务只有 spawn 时刻的上下文快照，看不到后续对话\n\n"
            "## 结果处理\n\n"
            "- 成功 → 系统消息通知结果内容\n"
            "- 失败 → 系统消息通知错误信息\n"
            "- 可以用 check_subagent(task_id=...) 主动查询\n\n"
            "## 案例\n\n"
            "spawn(task='搜索所有包含 TODO 的文件', label='find-todos')\n"
            "→ 后台搜索 TODO，完成后系统消息通知你结果\n\n"
            "spawn(\n"
            '    task="分析 src/utils.py 的结构",\n'
            '    label="utils-analysis",\n'
            '    output_schema=\'{"type": "object", "properties": {"classes": {"type": "array"}, "functions": {"type": "array"}}}\'\n'
            ")\n"
            "→ 后台分析模块，返回符合 schema 的结构化结果，便于你直接组合"
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
