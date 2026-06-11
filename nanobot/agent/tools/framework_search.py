"""Framework search tool — search authoritative framework docs and rules."""

from __future__ import annotations

from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        query=p(
            "string",
            "Natural-language query for semantic similarity search. "
            "Describe the framework rule, constraint, or behavior you want to find.",
        ),
        k=p("integer", "Number of results to return (default 5, max 20)",
            minimum=1, maximum=20, default=5),
        required=["query"],
    ),
)
class FrameworkSearchTool(Tool):
    """Search framework documentation and rules by semantic similarity (FAISS)."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "framework_search_tool"
    read_only = True

    description = (
        "**Purpose**: Search framework documentation and behavioral rules by "
        "**semantic similarity** (FAISS). Results are 100%% authoritative — "
        "must be followed.\n\n"
        "**Search type: semantic (FAISS vectors), NOT character/pattern matching**\n"
        "- Understands concepts — `'end turn'` matches `## Ending a Turn` section\n"
        "- Does NOT do substring matching — use grep_tool for exact search in framework/\n"
        "- Does NOT support | or boolean operators — write a natural-language phrase instead\n\n"
        "**When to use**:\n"
        "- You don't understand why the framework behaves a certain way\n"
        "- You're unsure what to do in a given situation\n"
        "- You need to confirm framework limits and rules\n"
        "- The user mentions a framework concept but you're unsure about the details\n\n"
        "**Difference from memory_search_tool**:\n"
        "- memory_search_tool: **semantic similarity** (FAISS) — searches experiential knowledge (for reference only)\n"
        "- framework_search_tool: **semantic similarity** (FAISS) — searches framework docs and rules (must be obeyed)\n\n"
        "**Difference from conversation_search_tool**:\n"
        "- conversation_search_tool: **character substring** (SQL LIKE) — finds exact text in past session history\n"
        "- framework_search_tool: **semantic similarity** (FAISS) — searches built-in framework documentation (authoritative)\n\n"
        "**Note**:\n"
        "- Results come from the framework/ directory's system documentation\n"
        "- All results are authoritative rules — do not question or skip them\n\n"
        "**Query tips — match the granularity of a section heading**:\n"
        "- Good: `turn lifecycle end turn rules` → matches `## Ending a Turn` section\n"
        "- Good: `subagent spawn constraints child agent` → specific terms for keyword boost\n"
        "- Avoid single vague words: `rules` alone returns too many matches\n\n"
        "**Examples**:\n"
        "  framework_search_tool(query='turn lifecycle end turn rules', k=5)\n"
        "  framework_search_tool(query='cron scheduled task constraints', k=5)\n"
        "  framework_search_tool(query='subagent spawn maximum count rules', k=5)"
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
