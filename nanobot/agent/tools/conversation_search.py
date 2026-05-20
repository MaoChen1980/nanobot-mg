"""Conversation search tool — search dialogue history and MEMORY.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        keyword=p("string", "Exact substring to match, case-insensitive"),
        query=p("string", "Alias for keyword. Provide this or keyword."),
        start=p("string", "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
        end=p("string", "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
    ),
)
class ConversationSearchTool(Tool):
    """Search conversation history and MEMORY.md for matching content."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "conversation_search"
    read_only = True

    description = (
        "**用途**: 搜索对话历史 — 从过去的对话记录中查找信息。\n\n"
        "**什么时候用**:\n"
        '- 用户说「之前讨论过」「上次说过」「以前做过」\n'
        "- 你感觉之前见过某个信息但记不清了\n"
        "- 需要精确关键词匹配历史记录\n\n"
        "**和 memory_search 的区别**:\n"
        "- memory_search 搜索知识库（语义匹配）\n"
        "- conversation_search 搜索对话历史记录（关键词子串匹配 + 时间过滤）\n\n"
        "**参数说明**:\n"
        "- keyword — 精确子串匹配，不区分大小写\n"
        "- start/end — 按时间范围过滤（YYYY-MM-DD 格式）\n"
        "- query — keyword 的别名，两个参数传一个即可\n\n"
        "**注意**:\n"
        "- 不搜索 goal/event/lesson 表（查询目标进度用 read_file(\"tasks/TREE.md\")）\n"
        "- 关键词是子串匹配，'deploy' 会匹配 'deployment'、'deploying' 等\n\n"
        "**示例**:\n"
        "  conversation_search(keyword='docker')\n"
        "  conversation_search(keyword='部署问题', start='2026-01-01')\n"
        "  conversation_search(query='error', start='2026-03-01', end='2026-03-15')"
    )

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
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

    @staticmethod
    def _in_date_range(timestamp: str, content: str, start: datetime | None, end: datetime | None) -> bool:
        if not start and not end:
            return True
        ts = ConversationSearchTool._parse_date(timestamp)
        if ts:
            if ts.tzinfo is None:
                ts = ts.astimezone()
            if (not start or ts >= start) and (not end or ts <= end):
                return True
        import re
        for match in re.finditer(r'\[(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2})', content):
            ct = ConversationSearchTool._parse_date(match.group(1))
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

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        keyword: str | None = None,
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> str:
        search_text = keyword or query
        if not search_text:
            return "Provide keyword (or query) to search for."

        start_dt = self._parse_date(start)
        end_dt = self._parse_date(end)
        if end_dt:
            end_dt = end_dt.replace(hour=23, minute=59, second=59)

        results: list[tuple[str, str]] = []

        # Search MEMORY.md
        memory = self._store.read_memory()
        if memory and self._match_text(memory, search_text):
            results.append(("", memory))

        # Search history via SQL
        if self._store._db is not None:
            db = self._store._db
            rows = db._conn.execute(
                "SELECT timestamp, content FROM history ORDER BY cursor"
            ).fetchall()

            # Also search current session messages
            from nanobot.agent.context_vars import _current_session_key
            current_key = _current_session_key.get()
            if current_key:
                msg_rows = db._conn.execute(
                    "SELECT timestamp, content FROM messages WHERE session_key = ? ORDER BY id",
                    (current_key,),
                ).fetchall()
                for ts, content in msg_rows:
                    if not self._in_date_range(ts, content, start_dt, end_dt):
                        continue
                    if not self._match_text(content, search_text):
                        continue
                    results.append((ts, content))
            for ts, content in rows:
                if not self._in_date_range(ts, content, start_dt, end_dt):
                    continue
                if not self._match_text(content, search_text):
                    continue
                results.append((ts, content))

        if not results:
            parts = "memories"
            if start:
                parts += f" from {start}"
            if end:
                parts += f" to {end}"
            return f"No memories found{'' if parts == 'memories' else f' {parts}'}."

        output = ["## Relevant Memories\n"]
        for ts, content in results[:50]:
            if ts:
                output.append(f"[{ts}] {content}")
            else:
                output.append(content)
            output.append("---")

        return "\n".join(output)
