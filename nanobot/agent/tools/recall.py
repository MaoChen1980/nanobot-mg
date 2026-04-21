"""Recall tool: search and retrieve relevant memories."""

from datetime import datetime
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "description": "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive",
            },
            "end": {
                "type": "string",
                "description": "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive",
            },
            "keyword": {
                "type": "string",
                "description": "Optional keyword to filter memories",
            },
        },
    }
)
class RecallTool(Tool):
    """Tool to search and retrieve relevant memories for enriching context."""

    def __init__(self, store: MemoryStore):
        self._store = store

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "MANDATORY before answering questions about past events: use this to search memories.\n\n"
            "Use when user asks about:\n"
            "- What was discussed or decided before\n"
            "- User's preferences, habits, or personal details\n"
            "- Past work, decisions, or projects\n"
            "- Dates, events, or facts from earlier conversations\n\n"
            "Parameters:\n"
            "- start: Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive\n"
            "- end: End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive\n"
            "- keyword: Optional keyword to filter\n\n"
            "Returns relevant snippets with timestamps.\n"
            "IMPORTANT: Do not dump raw results — synthesize into your answer."
        )

    @property
    def read_only(self) -> bool:
        return True

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse YYYY-MM-DD or YYYY-MM-DD HH:MM string to datetime."""
        if not date_str:
            return None
        # Try YYYY-MM-DD HH:MM first
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
        # Fall back to YYYY-MM-DD
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return None

    def _in_date_range(self, timestamp: str, start: datetime | None, end: datetime | None) -> bool:
        """Check if timestamp is within date range.

        Timestamp format: "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"
        For comparisons, we parse the full timestamp (including time if present)
        so that 2026-04-21 07:46 is correctly identified as within 2026-04-21.
        """
        # Try parsing full timestamp first
        ts = self._parse_date(timestamp)
        if not ts:
            return False
        if start and ts < start:
            return False
        if end and ts > end:
            return False
        return True

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

        # Search history.jsonl
        history_file = self._store.history_file
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        content = entry.get("content", "")

                        if not self._in_date_range(ts, start_dt, end_dt):
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
