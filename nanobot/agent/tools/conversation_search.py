"""Conversation search tool — search dialogue history in SQLite."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.memory_store import MemoryStore
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
    """Search conversation history — find information from past conversation records.

    Uses SQLite storage for persistent session history with keyword + date range filtering.
    """

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "conversation_search_tool"
    read_only = True

    description = (
        "**Purpose**: Search conversation history — find information from past conversation records.\n\n"
        "**When to use**:\n"
        '- The user says "we discussed this before", "I mentioned it last time", "we did this earlier"\n'
        "- You feel like you've seen some information before but can't recall it clearly\n"
        "- You need exact keyword matching against historical records\n\n"
        "**Difference from memory_search_tool**:\n"
        "- memory_search_tool searches the knowledge base (semantic matching)\n"
        "- conversation_search_tool searches conversation history (keyword substring match + time filter)\n\n"
        "**Parameters**:\n"
        "- keyword — exact substring match, case-insensitive\n"
        "- start/end — filter by time range (YYYY-MM-DD format)\n"
        "- query — alias for keyword, provide this or keyword\n\n"
        "**Note**:\n"
        "- Does not search goal/event/lesson tables (use read_file_tool(\"tasks/TREE.md\") to check goal progress)\n"
        "- Keywords use substring matching — 'deploy' will match 'deployment', 'deploying', etc.\n\n"
        "**Examples**:\n"
        "  conversation_search_tool(keyword='docker')\n"
        "  conversation_search_tool(keyword='deployment issue', start='2026-01-01')\n"
        "  conversation_search_tool(query='error', start='2026-03-01', end='2026-03-15')"
    )

    async def execute(
        self,
        keyword: str | None = None,
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> str:
        search_text = (keyword or query or "").strip()
        if not search_text:
            return "Error: Provide keyword (or query) to search for."

        results: list[dict[str, Any]] = []

        # Search MEMORY.md first
        memory = self._store.read_memory()
        if memory and search_text.lower() in memory.lower():
            results.append({
                "source": "memory",
                "timestamp": "",
                "content": memory[:2000],
            })

        # Search sessions via SQLite
        if self._store._db is not None:
            session_results = self._store._db.search_sessions(
                keyword=search_text,
                start=start,
                end=end,
                limit=50,
            )
            for r in session_results:
                content = r.get("content", "")
                if isinstance(content, (list, dict)):
                    content = json.dumps(content, ensure_ascii=False)
                results.append({
                    "source": "session",
                    "session_key": r.get("session_key", ""),
                    "role": r.get("role", ""),
                    "timestamp": r.get("timestamp", ""),
                    "content": content,
                })

        if not results:
            date_range = ""
            if start:
                date_range = f" from {start}"
            if end:
                date_range += f" to {end}"
            return f"No conversation history found{date_range}."

        # Format structured output
        output_parts: list[str] = ["## Conversation Search Results\n"]

        for i, r in enumerate(results[:50], 1):
            if r.get("source") == "memory":
                output_parts.append(f"### Result #{i} [memory]")
                output_parts.append("_Source: MEMORY.md_")
                content = r.get("content", "")
                output_parts.append(
                    content[:1000] + "..." if len(content) > 1000 else content
                )
            else:
                ts = r.get("timestamp", "")
                session = r.get("session_key", "")
                role = r.get("role", "")
                content = r.get("content", "")

                header = f"### Result #{i} [{ts}]"
                output_parts.append(header)

                meta: list[str] = []
                if role:
                    meta.append(f"role: {role}")
                if session:
                    meta.append(f"session: {session}")
                if meta:
                    output_parts.append(f"_{', '.join(meta)}_")

                # Truncate long content for readability
                truncated = content[:500] + "..." if len(content) > 500 else content
                output_parts.append(truncated)

            output_parts.append("---")

        output_parts.append(f"\n_Total: {len(results)} result(s)_")
        return "\n".join(output_parts)
