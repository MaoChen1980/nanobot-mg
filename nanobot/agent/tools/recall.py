"""Recall tool: search and retrieve relevant memories."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema


def _row_to_session_dict(row: tuple, cols: list[str]) -> dict:
    return dict(zip(cols, row, strict=False))


@tool_parameters(
    tool_parameters_schema(
        query=p("string", "Describe what you're looking for — searches all session history (recommended)"),
        keyword=p("string", "Exact substring match, case-insensitive"),
        start=p("string", "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
        end=p("string", "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
    )
)
class RecallTool(Tool):
    """Cross-session search: retrieve information from past conversations."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "recall"

    description = (
        "回忆：从你之前的对话中查找相关信息。\n\n"
        "**什么时候用**:\n"
        '- 用户说「之前讨论过」「上次说过」「以前做过」→ 先回忆一下再回答\n'
        "- 你感觉之前见过某个信息但记不清了 → 搜一下\n"
        "- 不确定答案想确认 → 先查查历史，不要猜\n\n"
        "**参数**:\n"
        "- query: 描述你想找什么（推荐），会搜索所有历史对话内容\n"
        "- keyword: 精确关键词（可选）\n"
        "- start/end: 限定时间范围（可选）\n\n"
        "**注意**: 不搜索 goal/event 表（查询目标进度用 list_goals）"
    )

    read_only = True

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse date string to datetime. Supports ISO 8601 and human formats."""
        if not date_str:
            return None
        # Try ISO 8601 first (may be naive or aware)
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt
        except ValueError:
            pass
        # Try YYYY-MM-DD HH:MM
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M").astimezone()
        except ValueError:
            pass
        # Fall back to YYYY-MM-DD
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").astimezone()
        except ValueError:
            return None

    def _in_date_range(self, timestamp: str, content: str, start: datetime | None, end: datetime | None) -> bool:
        """Check if timestamp or content timestamps are within date range.

        Timestamp format: ISO 8601, "YYYY-MM-DD HH:MM", or "YYYY-MM-DD".
        Primary check: the entry's own timestamp field.
        Secondary check: timestamps embedded in content (e.g. per-fact timestamps
        from consolidated archives, or time range prefixes).
        """
        # Primary: entry timestamp
        ts = self._parse_date(timestamp)
        if ts:
            if ts.tzinfo is None:
                ts = ts.astimezone()
            if (not start or ts >= start) and (not end or ts <= end):
                return True
        # Secondary: content timestamps
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
        """Case-insensitive substring match."""
        if not text:
            return True
        return text.lower() in content.lower()

    async def execute(
        self,
        query: str | None = None,
        keyword: str | None = None,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Search memory and history for relevant content."""
        start_dt = self._parse_date(start)
        end_dt = self._parse_date(end)

        if end_dt:
            end_dt = end_dt.replace(hour=23, minute=59, second=59)

        results: list[tuple[str, str]] = []

        # Combine query and keyword: both must match when both provided (AND)
        search_texts = [t for t in (query, keyword) if t]

        def matches(content: str) -> bool:
            return all(self._match_text(content, t) for t in search_texts)

        # Search MEMORY.md (no timestamp - always included if text matches)
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

        # Format results
        output = ["## Relevant Memories\n"]
        for ts, content in results[:50]:  # Limit to 50 entries
            if ts:
                output.append(f"[{ts}] {content}")
            else:
                output.append(content)
            output.append("---")

        return "\n".join(output)
