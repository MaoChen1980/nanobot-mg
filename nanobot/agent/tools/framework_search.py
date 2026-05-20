"""Framework search tool — search authoritative framework docs and rules."""

from __future__ import annotations

from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        query=p("string", "Natural-language query about framework behavior, constraints, or rules"),
        k=p("integer", "Number of results to return (default 5, max 20)",
            minimum=1, maximum=20, default=5),
        required=["query"],
    ),
)
class FrameworkSearchTool(Tool):
    """Search the framework documentation and behavioral rules (100% accurate, must follow)."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "framework_search"
    read_only = True

    description = (
        "**用途**: 搜索框架文档和行为规则 — 框架原理、约束、规则，100% 准确，必须遵守。\n\n"
        "**什么时候用**:\n"
        "- 不理解框架某个行为的原因\n"
        "- 遇到不确定该怎么做的情况\n"
        "- 需要确认框架的限制和规则\n"
        "- 用户提到框架概念但你不确定详情\n\n"
        "**和 memory_search 的区别**:\n"
        "- memory_search 搜索经验知识（仅供参考）\n"
        "- framework_search 搜索框架文档和规则（必须遵守）\n\n"
        "**和 conversation_search 的区别**:\n"
        "- conversation_search 搜索对话历史（事实记录）\n"
        "- framework_search 搜索系统内置框架文档（权威规则）\n\n"
        "**注意**:\n"
        "- 结果来自 framework/ 目录的系统文档\n"
        "- 所有结果均为权威规则，不要质疑或跳过\n\n"
        "**示例**:\n"
        "  framework_search(query='turn lifecycle 如何结束一轮对话')\n"
        "  framework_search(query='cron 定时任务约束')\n"
        "  framework_search(query='subagent spawn 规则')"
    )

    async def execute(self, query: str, k: int = 5, **kwargs: Any) -> str:
        query = query.strip()
        if not query:
            return "Please provide a query."

        results = self._store.framework_index.search(query, k=k)
        if not results:
            return "No matching framework documentation found."

        parts: list[str] = []
        for r in results:
            heading = r.get("heading", "")
            source = r.get("source", "")
            score = r.get("score", 0)

            label = f"{source}" if not heading else f"{source} — {heading}"
            parts.append(f"**{label}** [relevance={score:.2f}]")
            text = r.get("text", "")
            if len(text) > 400:
                text = text[:397] + "..."
            parts.append(f"> {text}\n")

        return "\n".join(parts)
