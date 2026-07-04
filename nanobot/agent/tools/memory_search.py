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
        query=p(
            "string",
            "Natural-language query for semantic similarity search. "
            "Describe what you want to find — this is NOT character/pattern matching.",
        ),
        k=p("integer", "Number of results to return (default 5, max 20)",
            minimum=1, maximum=20, default=5),
        required=["query"],
    ),
)
class MemorySearchTool(Tool):
    """Search the memory/ knowledge base by semantic similarity (FAISS vectors)."""

    def __init__(self, store: MemoryStore):
        self._store = store
    instruction = (
        "Search the knowledge base for historical experiences/decisions. "
        "Use when user references past experience. Not for code search — use grep/glob for that."
    )

    name = "memory_search"
    read_only = True

    description = (
        "memory_search: Search the knowledge base (memory/) by semantic similarity (FAISS). "
        "Understands concepts — 'deploy failure' finds 'rollback issues'. "
        "Also searches tasks/ and skills/ indexes. "
        "Returns source file, heading, score, text excerpt, and cross-references."
    )

    async def execute(self, query: str, k: int = 5, **kwargs: Any) -> str:
        query = query.strip()
        if not query:
            return "Please provide a query."

        results = self._store.vector_index.search(query, k=k)

        # Also search tasks/ index if available
        tasks_results: list[dict[str, Any]] = []
        tasks_index = getattr(self._store, "tasks_index", None)
        if tasks_index is not None:
            tasks_results = tasks_index.search(query, k=min(k, 3))

        # Also search skills index if available
        skills_results: list[dict[str, Any]] = []
        skills_index = getattr(self._store, "skills_index", None)
        if skills_index is not None:
            skills_results = skills_index.search(query, k=min(k, 3))

        if not results and not tasks_results and not skills_results:
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
                                refs.append(f"[{title}](memory/{path}): {excerpt}")
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

            label = f"memory/{source}" if not heading else f"memory/{source} — {heading}"
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

        if tasks_results:
            if parts:
                parts.append("---")
            for r in tasks_results:
                source = r.get("source", "")
                heading = r.get("heading", "")
                score = r.get("score", 0)
                label = f"tasks/{source} — {heading}" if heading else f"tasks/{source}"
                parts.append(f"**{label}** [score={score:.2f}]")
                text = r.get("text", "")
                if len(text) > 400:
                    text = text[:397] + "..."
                parts.append(f"> {text}\n")

        if skills_results:
            if parts:
                parts.append("---")
            for r in skills_results:
                source = r.get("source", "")
                score = r.get("score", 0)
                label = f"skills/{source}"
                parts.append(f"**{label}** [score={score:.2f}]")
                text = r.get("text", "")
                if len(text) > 400:
                    text = text[:397] + "..."
                parts.append(f"> {text}\n")

        return "\n".join(parts)