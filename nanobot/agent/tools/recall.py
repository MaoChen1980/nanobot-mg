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
        start=p("string", "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
        end=p("string", "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive"),
        keyword=p("string", "Optional keyword to filter memories"),
    )
)
class RecallTool(Tool):
    """Tool to search and retrieve relevant memories for enriching context."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "recall"

    description = (
        "**用途**: 搜索历史记忆，用于回答关于过去事件的问题。"
        "搜索范围：session 历史记录（SQLite 或 history.jsonl）+ MEMORY.md。\n\n"
        "**限制**:\n"
        "- 最多返回 50 条结果\n"
        "- keyword 不区分大小写，多个关键词用空格分隔（OR 逻辑）\n"
        "- MEMORY.md 内容无时间戳，只要 keyword 匹配就返回\n\n"
        "**错误应对**:\n"
        "- 无结果 → 返回 \"No memories found\" + 日期范围提示\n"
        "- 日期格式无法解析 → 尝试多种格式（ISO 8601、YYYY-MM-DD HH:MM、YYYY-MM-DD）\n\n"
        "**边界条件**:\n"
        "- 只需要当前会话内容 → 不用 recall，检查历史即可\n"
        "- 时间范围默认 inclusive（end 自动延至 23:59:59）\n"
        "- 无 keyword → 返回时间范围内的所有记忆\n\n"
        "**极简案例**: recall(keyword='openclaw')\n"
        "→ 搜索包含 'openclaw' 的所有记忆"
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

    def _match_keyword(self, content: str, keyword: str | None) -> bool:
        """Check if content matches keyword (case-insensitive).

        Supports multiple keywords separated by spaces.
        Uses OR logic: content matches if ANY keyword is found.
        """
        if not keyword:
            return True
        content_lower = content.lower()
        # Split by whitespace and match if ANY keyword is found
        keywords = keyword.lower().split()
        return any(kw in content_lower for kw in keywords)

    async def execute(
        self,
        start: str | None = None,
        end: str | None = None,
        keyword: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Search memory and history for relevant content."""
        import json

        start_dt = self._parse_date(start)
        end_dt = self._parse_date(end)

        if end_dt:
            # Make end inclusive (end of day)
            end_dt = end_dt.replace(hour=23, minute=59, second=59)

        results: list[tuple[str, str]] = []  # (timestamp, content)

        # Search MEMORY.md (no timestamp - always included if keyword matches)
        memory = self._store.read_memory()
        if memory and self._match_keyword(memory, keyword):
            results.append(("", memory))

        # Search history — use SQL if DB available, else scan file
        history_file = self._store.history_file
        if self._store._db is not None:
            db = self._store._db
            rows = db._conn.execute(
                "SELECT timestamp, content FROM history ORDER BY cursor"
            ).fetchall()
            for ts, content in rows:
                if not self._in_date_range(ts, content, start_dt, end_dt):
                    continue
                if not self._match_keyword(content, keyword):
                    continue
                results.append((ts, content))
        elif history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        content = entry.get("content", "")

                        if not self._in_date_range(ts, content, start_dt, end_dt):
                            continue
                        if not self._match_keyword(content, keyword):
                            continue

                        results.append((ts, content))
                    except json.JSONDecodeError:
                        continue

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
