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
            "**用途**: 批量启动多个子任务并行执行。一次调用启动多个独立子任务，每个在后台独立运行。\n\n"
            "## 工作机制\n\n"
            "- 接受任务数组，每个任务独立 spawn\n"
            "- 所有任务同时启动，互不依赖\n"
            "- 每个任务完成后各自异步通知结果\n"
            "- 可以用 check_subagent 或 list_subagents 查询进度\n\n"
            "## 什么时候用\n\n"
            "- 有多个独立、可并行的子任务需要处理\n"
            "- 需要将一个大任务分解为多个独立步骤\n"
            "- 需要同时对多个文件/模块做相同类型的分析\n\n"
            "## 什么时候不用\n\n"
            "- 任务之间有依赖关系 → 用 spawn 单独控制\n"
            "- 只有一个子任务 → 直接用 spawn\n"
            "- 需要同步结果 → 自己做，不要 spawn\n\n"
            "## 限制\n\n"
            "- 每个任务遵守 spawn 的相同限制\n"
            "- 任务之间不能互相通信\n"
            "- 结果到达顺序不确定\n\n"
            "## 案例\n\n"
            "spawn_many(tasks=[\n"
            '    {"task": "分析 module A 的结构", "label": "mod-a"},\n'
            '    {"task": "分析 module B 的结构", "label": "mod-b"},\n'
            '    {"task": "分析 module C 的结构", "label": "mod-c", "output_schema": \'{"type": "object", "properties": {"classes": {"type": "array"}}}\'},\n'
            "])\n"
            "→ 三个子任务同时启动，各自完成后通知结果"
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
