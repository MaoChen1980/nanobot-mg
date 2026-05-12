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
            "MANDATORY before answering questions about past events: use this to search memories.\n\n"
            "You tend to forget: past decisions, user preferences, what was agreed, what was tried.\n\n"
            "Use when:\n"
            "- User says 'as we discussed', 'remember when', 'earlier we'\n"
            "- User references a past project, decision, or conversation\n"
            "- You feel like you've had this conversation before but can't recall details\n"
            "- User's behavior seems inconsistent with what they asked before\n\n"
            "Parameters:\n"
            "- start: Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive\n"
            "- end: End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive\n"
            "- keyword: Optional keyword to filter\n\n"
            "Examples:\n"
            "- 'what did the user say about OpenClaw architecture?' → recall(keyword='openclaw')\n"
            "- 'what was the final decision on MEMORY.md management?' → recall(keyword='MEMORY.md')\n"
            "- 'summarize changes made on 2026-04-28' → recall(start='2026-04-28')\n"
            "- 'trace the SOUL.md rewrite discussion' → recall(keyword='SOUL.md', start='2026-04-28')\n\n"
            "Returns relevant snippets with timestamps (max 50 entries).\n"
            "IMPORTANT: Do not dump raw results — synthesize into your answer.\n\n"
            "Without this tool, you work with no memory of the user or past sessions."
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
