"""Recall tool — search conversation history or knowledge memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema


def _row_to_session_dict(row: tuple, cols: list[str]) -> dict:
    return dict(zip(cols, row, strict=False))


def _find_line_range(full_text: str, chunk_text: str) -> tuple[int, int]:
    """Find (start_line, end_line) of *chunk_text* inside *full_text* (1-based)."""
    if not full_text or not chunk_text:
        return (0, 0)

    idx = full_text.find(chunk_text)
    if idx == -1:
        first = chunk_text.split("\n")[0].strip()
        if first:
            idx = full_text.find(first)
    if idx == -1:
        return (0, 0)

    start_line = full_text[:idx].count("\n") + 1
    end_line = start_line + chunk_text.count("\n")
    return (start_line, end_line)


@tool_parameters(
    tool_parameters_schema(
        query=p("string", "Describe what you're looking for. Used for both history and knowledge modes."),
        mode=p("string",
            "Search mode:\n"
            "  'history' — 搜索对话/执行细节。支持 keyword（精确关键词）和 start/end（时间范围）。\n"
            "  'knowledge' — 搜索经验/知识。语义搜索 memory/ 目录下的 .md 文件。",
            enum=["history", "knowledge"], default="history",
        ),
        keyword=p("string", "Exact substring match, case-insensitive (history mode only)"),
        start=p("string", "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive (history mode only)"),
        end=p("string", "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive (history mode only)"),
        k=p("integer", "Number of results to return (knowledge mode only, default 5, max 20)",
            minimum=1, maximum=20, default=5),
    ),
    required=["query", "mode"],
)
class RecallTool(Tool):
    """Cross-session search: retrieve information from past conversations or
    knowledge memory."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "recall"

    description = (
        "召回：从对话历史或持久化记忆中查找信息。\n\n"
        "**什么时候用**:\n"
        '- 用户说「之前讨论过」「上次说过」「以前做过」→ "这个以前讨论过"\n'
        "- 你感觉之前见过某个信息但记不清了 → 搜一下\n"
        "- 想查项目经验/知识积累 → 用 knowledge 模式\n\n"
        "**两种模式（mode 参数）**:\n\n"
        "**mode='history'**: 搜索对话/执行细节\n"
        "- 搜索 MEMORY.md + 全部历史对话\n"
        "- 支持 keyword（精确关键词子串匹配）\n"
        "- 支持 start/end（时间范围过滤）\n"
        "- 适合：\"之前讨论过什么\"\"上次报了什么错\"\n\n"
        "**mode='knowledge'**: 搜索经验/知识\n"
        "- 用语义嵌入（向量）搜索 memory/ 目录下的 .md 文件\n"
        "- 按语义相关性返回结果，不是关键词匹配\n"
        "- 支持 k 参数控制返回数量\n"
        "- 适合：\"相关的技术方案是什么\"\"类似问题的经验教训\"\n\n"
        "**选择指南**:\n"
        "- 知道精确关键词 → history + keyword\n"
        "- 想找没有明确关键词的知识 → knowledge\n"
        "- 完全没头绪 → 先 knowledge 再 history\n\n"
        "**注意**:\n"
        "- 不搜索 goal/event 表（查询目标进度用 list_goals）\n"
        "- knowledge 模式新建或修改的内容不会立刻出现，需要等索引重建\n"
        "- knowledge 模式是语义模糊匹配，可能漏掉特定术语\n\n"
        "**极简案例**:\n"
        "  recall(mode='knowledge', query='memory consolidation', k=5)\n"
        "  → 返回语义相关的知识记忆段落\n"
        "  recall(mode='history', query='部署问题', keyword='docker', start='2026-01-01')\n"
        "  → 返回 2026 年以来含 docker 的对话记录"
    )

    read_only = True

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse date string to datetime. Supports ISO 8601 and human formats."""
        if not date_str:
            return None
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt
        except ValueError:
            pass
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M").astimezone()
        except ValueError:
            pass
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").astimezone()
        except ValueError:
            return None

    def _in_date_range(self, timestamp: str, content: str, start: datetime | None, end: datetime | None) -> bool:
        if not start and not end:
            return True
        ts = self._parse_date(timestamp)
        if ts:
            if ts.tzinfo is None:
                ts = ts.astimezone()
            if (not start or ts >= start) and (not end or ts <= end):
                return True
        import re
        for match in re.finditer(r'\[(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2})', content):
            ct = self._parse_date(match.group(1))
            if ct:
                if ct.tzinfo is None:
                    ct = ct.astimezone()
                if (not start or ct >= start) and (not end or ct <= end):
                    return True
        return False

    @staticmethod
    def _match_text(content: str, text: str | None) -> bool:
        if not text:
            return True
        return text.lower() in content.lower()

    async def execute(
        self,
        query: str,
        mode: str = "history",
        keyword: str | None = None,
        start: str | None = None,
        end: str | None = None,
        k: int = 5,
        **kwargs: Any,
    ) -> str:
        if mode == "history":
            return self._search_history(query, keyword, start, end)
        elif mode == "knowledge":
            return self._search_knowledge(query, k)
        return f"Error: Unknown mode '{mode}'. Use 'history' or 'knowledge'."

    # ------------------------------------------------------------------
    # history mode — conversation/execution details
    # ------------------------------------------------------------------

    def _search_history(
        self,
        query: str | None = None,
        keyword: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> str:
        """Search MEMORY.md and SQLite history for matching content."""
        start_dt = self._parse_date(start)
        end_dt = self._parse_date(end)

        if end_dt:
            end_dt = end_dt.replace(hour=23, minute=59, second=59)

        results: list[tuple[str, str]] = []

        search_texts = [t for t in (query, keyword) if t]

        def matches(content: str) -> bool:
            return all(self._match_text(content, t) for t in search_texts)

        # Search MEMORY.md
        memory = self._store.read_memory()
        if memory and matches(memory):
            results.append(("", memory))

        # Search history via SQL
        if self._store._db is not None:
            db = self._store._db
            rows = db._conn.execute(
                "SELECT timestamp, content FROM history ORDER BY cursor"
            ).fetchall()
            for ts, content in rows:
                if not self._in_date_range(ts, content, start_dt, end_dt):
                    continue
                if not matches(content):
                    continue
                results.append((ts, content))

        if not results:
            date_hint = ""
            if start:
                date_hint += f" from {start}"
            if end:
                date_hint += f" to {end}"
            return f"No memories found{date_hint}."

        output = ["## Relevant Memories\n"]
        for ts, content in results[:50]:
            if ts:
                output.append(f"[{ts}] {content}")
            else:
                output.append(content)
            output.append("---")

        return "\n".join(output)

    # ------------------------------------------------------------------
    # knowledge mode — semantic search over memory/ directory
    # ------------------------------------------------------------------

    def _search_knowledge(self, query: str, k: int = 5) -> str:
        """Semantic search over memory/ directory files via vector index."""
        results = self._store.vector_index.search(query, k=k)
        if not results:
            return "No relevant knowledge found."

        for r in results:
            source = r.get("source", "")
            if source:
                full = self._store.read_categorized_file(source)
                r["start_line"], r["end_line"] = _find_line_range(full, r.get("text", ""))

        parts: list[str] = []
        for r in results:
            heading = r.get("heading", "")
            source = r.get("source", "")
            score = r.get("score", 0)
            start = r.get("start_line", 0)
            end = r.get("end_line", 0)

            label = f"{source}" if not heading else f"{source} — {heading}"
            loc = f" (lines {start}-{end})" if start and end else ""
            parts.append(f"**{label}** [score={score:.2f}]{loc}")
            text = r.get("text", "")
            if len(text) > 400:
                text = text[:397] + "..."
            parts.append(f"> {text}\n")

        return "\n".join(parts)
