"""Semantic search tool: search_text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.tools._semantic_base import (
    chunk_text,
    compute_similarity,
    get_model,
)
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Tool 2: search_text
# ---------------------------------------------------------------------------


@tool_parameters(
    build_parameters_schema(
        query=p("string", "Natural-language query to find relevant passages"),
        text=p("string", "Text content to search within (max 5 MB). Provide exactly one of text or path (not both, not neither)."),
        path=p("string", "Absolute path to a file to read and search. Provide exactly one of text or path (not both, not neither)."),
        k=p("integer", "Number of results to return (default 5)", minimum=1, maximum=20, default=5),
        required=["query"],
    ),
)
class SearchTextTool(Tool):
    """Search a block of text semantically."""

    name = "search_text_tool"
    read_only = True

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    description = (
        "**Purpose**: Semantically search for relevant passages in a given text or file "
        "without reading the entire document. Pass either text or path (not both).\n\n"
        "**Search type: semantic (vector embeddings), NOT pattern matching**\n"
        "- Understands concepts and meaning — `query='timeout handling'` can find "
        "passages about 'retry logic', 'deadline exceeded', or 'connection reset'\n"
        "- Does NOT do substring/regex matching — use grep_tool for exact patterns\n\n"
        "**When to use**:\n"
        "- You know roughly what you're looking for but not the exact keywords or symbols\n"
        "- You're exploring an unfamiliar codebase and want to find e.g. 'timeout handling' "
        "without knowing the function name\n"
        "- A long document covers many topics and you want to find passages related "
        "to a specific concept (security, auth, encryption, etc.)\n\n"
        "**When NOT to use**:\n"
        "- You already know the exact keyword or symbol → use grep_tool\n"
        "- You want to read the entire file from start to finish → use read_file_tool\n\n"
        "**Query tips**:\n"
        "- Combine a concept with domain-specific terms for better results\n"
        "- 2-5 specific words usually works better than a full sentence\n\n"
        "**Examples**:\n"
        "  search_text_tool(query='error handling timeout logic', path='/src/main.py')\n"
        "  search_text_tool(query='authentication encryption security', text='...')\n"
        "  search_text_tool(query='configuration setup initialization', path='/app/config.py')\n"
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

        model = get_model()
        if model is None:
            return "Semantic search is unavailable — sentence-transformers not installed"

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
