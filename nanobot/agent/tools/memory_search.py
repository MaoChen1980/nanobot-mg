"""Memory search tool — semantic search over the memory/ knowledge base."""

from __future__ import annotations

import re
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


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
    build_parameters_schema(
        query=p("string", "Natural-language query to find relevant knowledge passages"),
        k=p("integer", "Number of results to return (default 5, max 20)",
            minimum=1, maximum=20, default=5),
        required=["query"],
    ),
)
class MemorySearchTool(Tool):
    """Search across the memory/ knowledge base using semantic similarity."""

    def __init__(self, store: MemoryStore):
        self._store = store

    name = "memory_search"
    read_only = True

    description = (
        "**Purpose**: Search the persistent knowledge base (memory/ directory) by semantic similarity.\n\n"
        "**When to use**:\n"
        '- The user says "we encountered this before", "we learned this last time", "we did something similar"\n'
        "- You need to recall accumulated knowledge, experience, or decisions\n"
        "- You want to find historical records related to the current topic\n\n"
        "**Difference from search_text**:\n"
        "- search_text searches the single text snippet or file you pass in\n"
        "- memory_search searches the entire memory/ knowledge base (FAISS vector index)\n\n"
        "**Difference from conversation_search**:\n"
        "- conversation_search searches conversation history (keyword match + time filter)\n"
        "- memory_search searches the knowledge base (semantic similarity match)\n\n"
        "**Note**:\n"
        "- New or modified knowledge takes up to 2 hours to appear in the index\n"
        "- This is fuzzy semantic matching — may miss specific terms. Use grep for exact keywords.\n\n"
        "**Examples**:\n"
        "  memory_search(query='memory consolidation', k=5)\n"
        "  memory_search(query='deployment best practices')"
    )

    async def execute(self, query: str, k: int = 5, **kwargs: Any) -> str:
        query = query.strip()
        if not query:
            return "Please provide a query."

        results = self._store.vector_index.search(query, k=k)
        if not results:
            return "No relevant knowledge found."

        for r in results:
            source = r.get("source", "")
            if source:
                full = self._store.read_categorized_file(source)
                r["start_line"], r["end_line"] = _find_line_range(full, r.get("text", ""))

                # Backlink traversal: for high-score results, follow ## See also links
                score = r.get("score", 0)
                if score > 0.5 and full:
                    see_also = re.search(
                        r"\n## See also\n(.*?)(?=\n## |\Z)", full, re.DOTALL,
                    )
                    if see_also:
                        links = re.findall(
                            r"\[([^\]]+)\]\(([^)]+)\)", see_also.group(1),
                        )
                        refs: list[str] = []
                        for title, path in links[:2]:
                            ref_path = self._store.memory_dir / path
                            if not ref_path.exists():
                                continue
                            try:
                                excerpt = ref_path.read_text(encoding="utf-8")[:150].strip().replace("\n", " ")
                                refs.append(f"[{title}]({path}): {excerpt}")
                            except OSError:
                                continue
                        if refs:
                            r["crossrefs"] = refs

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

            crossrefs = r.get("crossrefs")
            if crossrefs:
                for ref in crossrefs:
                    parts.append(f"  Related: {ref}")

        return "\n".join(parts)
