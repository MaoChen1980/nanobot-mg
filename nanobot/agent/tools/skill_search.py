"""Skill search tool — semantic search over workspace and built-in skills."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        query=p(
            "string",
            "Natural-language query for semantic similarity search. "
            "Describe what skill you need — this is NOT character/pattern matching.",
        ),
        k=p("integer", "Number of results to return (default 6, max 15)",
            minimum=1, maximum=15, default=6),
        required=["query"],
    ),
)
class SkillSearchTool(Tool):
    """Search workspace and built-in skills by semantic similarity (FAISS vectors)."""

    def __init__(self, store) -> None:
        self._store = store

    instruction = (
        "Search existing skills (workspace + built-in) by semantic similarity. "
        "Use `read_file <path>` (from result) to load a matching skill. "
        "Use before creating a new skill to check for duplicates, "
        "or during a task to find applicable skills."
    )

    name = "skill_search"
    read_only = True

    description = (
        "skill_search: Search available skills (workspace + built-in) by semantic similarity (FAISS). "
        "Understands concepts — 'market analysis' finds 'market-game-analysis'. "
        "Returns skill name, description, file path, and similarity score. "
        "Use to find applicable skills for the current task, or before creating/updating a skill."
        "\n\nOutput example:\n"
        "  **market-game-analysis** [score=0.91]\n"
        "  > Market analysis for competitive strategy"
    )

    async def execute(self, query: str, k: int = 6, **kwargs: Any) -> str:
        query = query.strip()
        if not query:
            return "Please provide a query."

        # Ensure index is up to date with any SKILL.md changes
        if hasattr(self._store, "refresh_skills_index"):
            self._store.refresh_skills_index()

        skills_index = getattr(self._store, "skills_index", None)
        if skills_index is None:
            return "No skills index available."

        self._store.ensure_skills_index()
        results = skills_index.search(query, k=k)
        if not results:
            return "No relevant skills found."

        parts: list[str] = []
        for r in results:
            source = r.get("source", "")
            score = r.get("score", 0)
            # source = "skills/{name}.md" → extract name
            skill_name = source.replace("skills/", "").replace(".md", "") if source else "?"
            parts.append(f"**{skill_name}** [score={score:.2f}]")
            text = r.get("text", "")
            if len(text) > 400:
                text = text[:397] + "..."
            parts.append(f"> {text}\n")

        return "\n".join(parts)
