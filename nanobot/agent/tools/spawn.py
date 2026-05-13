"""Spawn tool for creating background subagents."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema
from nanobot.agent.context_vars import _current_messages_for_subagent

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=p("string", "The task for the subagent to complete"),
        label=p("string", "Optional short label for the task (for display)"),
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
            "**用途**: 生成子 agent 在后台执行独立任务，完成后报告结果。\n\n"
            "**限制**:\n"
            "- 子 agent 有独立隔离的会话，无法访问主对话\n"
            "- 最多 30 次工具调用迭代\n"
            "- 子 agent 不能嵌套 spawn\n"
            "- 子 agent 没有 skills 访问权限\n"
            "- 没有 skills 参数 — 子 agent 不继承主 agent 的 skills\n"
            "- 可用工具：read_file, list_dir, glob, grep, write_file, edit_file, web_search, web_fetch, exec\n"
            "- 选择标准：基础文件/网络操作工具，不依赖 nanobot 内部状态或主 agent 内存\n\n"
            "**错误应对**:\n"
            "- 任务失败 → 子 agent 返回错误信息\n"
            "- 结果为空 → 子 agent 报告无结果\n\n"
            "**边界条件**:\n"
            "- 任务需要你的中间决策 → 不要用 spawn（子 agent 无法咨询你）\n"
            "- 后续步骤依赖结果 → 不要用 spawn，直接做\n"
            "- 创建外部资源/账户 → 不要用 spawn（子 agent 无权）\n\n"
            "**极简案例**: spawn(task='搜索所有包含 \"TODO\" 的文件', label='find-todos')\n"
            "→ 后台搜索 TODO，完成后报告结果"
        )

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
            logger.debug("Failed to read workspace file {} for subagent context", filename)
            pass
        return ""
