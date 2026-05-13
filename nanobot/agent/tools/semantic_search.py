"""Semantic search tools: search_memory and search_text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools._semantic_base import (
    chunk_text,
    compute_similarity,
    get_model,
)
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema

_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Tool 1: search_memory
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        query=p("string", "Natural-language query to find relevant memory content"),
        k=p("integer", "Number of results to return", minimum=1, maximum=20),
    ),
    required=["query"],
)
class SearchMemoryTool(Tool):
    """Search memory files semantically."""

    name = "search_memory"
    read_only = True

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    description = (
        "Search memory files (long-term knowledge) by meaning, not keywords.\n\n"
        "Use this when:\n"
        "- You vaguely recall past conversations or notes but can't remember exact words\n"
        "- grep returned nothing because the phrasing differs\n"
        "- You want to check if memory already has relevant info before asking the user\n\n"
        "Do NOT use when:\n"
        "- You need exact keyword matches — use grep instead\n"
        "- You know the exact file path — just use read_file\n"
        "- You need to search within a single text/document — use search_text instead\n"
        "- You need a table of contents preview — use inspect_text instead\n\n"
        "Results: each has a score (0-1), source file, and line range.\n"
        "How to act:\n"
        "- score > 0.6: highly relevant — use read_file to read the full section\n"
        "- score 0.3-0.6: possibly relevant — consider reading for context\n"
        "- score < 0.3: weak match — try a different query, or grep, or skip\n\n"
        "Limitations:\n"
        "- Only searches memory/ directory (markdown files)\n"
        "- Newly added content may not be indexed yet\n"
        "- k max 20 results per query\n"
        "- NOT for code search — code keywords don't match BGE embeddings well"
    )

    async def execute(self, query: str, k: int = 5, **kwargs: Any) -> str:
        results = self._store.vector_index.search(query, k=k)
        if not results:
            return "No relevant memory found."

        # Compute line numbers for each result
        for r in results:
            source = r.get("source", "")
            if source:
                full = self._store.read_categorized_file(source)
                r["start_line"], r["end_line"] = _find_line_range(full, r.get("text", ""))

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

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 2: search_text
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        query=p("string", "Natural-language query to find relevant passages"),
        text=p("string", "Text content to search within (max 5 MB). Provide this or path."),
        path=p("string", "File path to read and search. Provide this or text."),
        k=p("integer", "Number of results to return", minimum=1, maximum=20),
    ),
    required=["query"],
)
class SearchTextTool(Tool):
    """Search a block of text semantically."""

    name = "search_text"
    read_only = True

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    description = (
        "Find relevant passages in a single text block by meaning, without reading it all. "
        "Operates on one text you provide (inline or file path).\n\n"
        "Use this when:\n"
        "- web_fetch returned a long article and you only need parts relevant to "
        "your question\n"
        "- You received a large API response, document, or message and need "
        "specific details\n"
        "- You want to extract just the useful parts from a long text\n\n"
        "Do NOT use when:\n"
        "- You need exact keyword matches — use grep instead\n"
        "- You don't yet know what the text contains — use inspect_text first\n"
        "- You need to search across memory/ directory — use search_memory instead\n\n"
        "Results: each has a score (0-1), char offset, and length.\n"
        "How to act:\n"
        "- score > 0.6: relevant — read it with text[offset:offset+length]\n"
        "- score 0.3-0.6: possibly relevant — read for context\n"
        "- score < 0.3: weak match — try different query or use inspect_text\n"
        "- If all scores are < 0.3, the text probably doesn't contain what you need\n\n"
        "Limits: 5 MB max input text, k max 20 results.\n"
        "You can pass the text directly, or pass a path to a file on disk.\n\n"
        "Limitations:\n"
        "- Max 5 MB — larger inputs are rejected\n"
        "- NOT for code (use grep instead)\n"
        "- Semantic search ≈ fuzzy matching — not guaranteed to find every mention\n"
        "- Content at chunk boundaries may be split across results"
    )

    async def execute(
        self, query: str, text: str | None = None, path: str | None = None,
        k: int = 5, **kwargs: Any,
    ) -> str:
        if not text and not path:
            return "Provide either text or path."
        if text and path:
            return "Provide either text or path, not both."

        if path:
            from nanobot.agent.tools.filesystem.filesystem_base import _resolve_path
            try:
                resolved = _resolve_path(path, self._workspace, self._allowed_dir)
                raw = resolved.read_bytes()
                if len(raw) > _MAX_TEXT_BYTES:
                    return f"File too large ({len(raw)} bytes). Maximum is {_MAX_TEXT_BYTES // (1024 * 1024)} MB."
                text = raw.decode("utf-8")
            except Exception as e:
                return f"Cannot read file: {e}"

        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            return (
                f"text too large ({len(text)} chars / "
                f"{len(text.encode('utf-8'))} bytes). "
                f"Maximum is {_MAX_TEXT_BYTES // (1024 * 1024)} MB. "
                "Try narrowing the input or using grep on part of it."
            )
            return (
                f"text too large ({len(text)} chars / "
                f"{len(text.encode('utf-8'))} bytes). "
                f"Maximum is {_MAX_TEXT_BYTES // (1024 * 1024)} MB. "
                "Try narrowing the input or using grep on part of it."
            )

        model = get_model()
        if model is None:
            return (
                "Semantic search is unavailable — sentence-transformers "
                "is not installed. "
                "Install with: pip install nanobot-ai[memory-vector]"
            )

        chunks = chunk_text(text)
        if not chunks:
            return "Empty text — nothing to search."

        results = compute_similarity(query, chunks, model, k=k)
        if not results:
            return "No relevant passages found."

        parts: list[str] = []
        for r in results:
            score = r.get("score", 0)
            offset = r.get("start_char", 0)
            length = r.get("end_char", 0) - r.get("start_char", 0)
            snippet = r.get("text", "")
            if len(snippet) > 400:
                snippet = snippet[:397] + "..."
            parts.append(
                f"**Passage** [score={score:.2f}, offset={offset}, length={length}]"
            )
            parts.append(f"> {snippet}\n")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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
