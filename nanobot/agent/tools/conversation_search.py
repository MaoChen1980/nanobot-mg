"""Conversation search tool — search dialogue history in SQLite."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.memory_store import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        keyword=p(
            "string",
            "Character substring to match, case-insensitive. "
            "Use | to OR multiple terms (e.g. 'deploy|rollback'). "
            "This is NOT semantic search — it matches the literal characters.",
        ),
        query=p(
            "string",
            "Deprecated alias for keyword. Will be removed — use keyword instead.",
        ),
        start=p(
            "string",
            "Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive",
        ),
        end=p(
            "string",
            "End date (YYYY-MM-DD or YYYY-MM-DD HH:MM), inclusive",
        ),
        required=["keyword"],
    ),
)
class ConversationSearchTool(Tool):
    """Search conversation history via character substring matching (SQL LIKE)."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "conversation_search_tool"
    read_only = True

    description = (
        "**Purpose**: Search past conversation history by character substring matching "
        "(SQL LIKE).  NOT semantic — matches the exact characters you type.\n\n"
        "**When to use**:\n"
        '- The user says "we discussed this before", "I mentioned it last time", "we did this earlier"\n'
        "- You feel like you've seen some information before but can't recall it clearly\n"
        "- You need to find a specific fact, error message, or topic from past sessions\n\n"
        "**Search type: character substring (LIKE), NOT semantic**\n"
        "- `keyword='deploy'` matches any message containing those characters: 'deployment', 'redeploy', 'deploying' etc.\n"
        "- Does NOT understand synonyms or concepts — use memory_search_tool for semantic matching\n"
        "- Use grep_tool on memory/ files if you need exact regex matching\n\n"
        "**Multiple keywords (OR)**: separate with `|`\n"
        "- `keyword='deploy|rollback'` matches messages containing 'deploy' OR 'rollback'\n"
        "- Works with both keyword and query parameters\n"
        "- Spaces around `|` are trimmed automatically\n\n"
        "**Difference from memory_search_tool**:\n"
        "- memory_search_tool: **semantic similarity** (FAISS vectors) — understands concepts\n"
        "- conversation_search_tool: **character substring** (SQL LIKE) — matches exact text\n\n"
        "**Difference from framework_search_tool**:\n"
        "- framework_search_tool: **semantic similarity** (FAISS vectors) — for authoritative rules\n"
        "- conversation_search_tool: **character substring** (SQL LIKE) — for historical records\n\n"
        "**Parameters**:\n"
        "- keyword — character substring(s) to match, case-insensitive. Use `|` for OR\n"
        "- query — alias for keyword, provide this or keyword\n"
        "- start — filter from this date inclusive (YYYY-MM-DD)\n"
        "- end — filter to this date inclusive (YYYY-MM-DD)\n\n"
        "**Examples**:\n"
        "  conversation_search_tool(keyword='docker')\n"
        "  conversation_search_tool(keyword='deploy|rollback', start='2026-01-01')\n"
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

        # Split | for OR logic, used for MEMORY.md matching
        or_terms = [t.strip().lower() for t in search_text.split("|") if t.strip()]
        if not or_terms:
            return "Error: Provide keyword (or query) to search for."

        results: list[dict[str, Any]] = []

        # Search MEMORY.md — match if ANY or_term is found
        memory = self._store.read_memory()
        if memory:
            memory_lower = memory.lower()
            if any(term in memory_lower for term in or_terms):
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
