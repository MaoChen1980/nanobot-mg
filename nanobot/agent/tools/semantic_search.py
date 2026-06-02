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
        text=p("string", "Text content to search within (max 5 MB). Provide this or path."),
        path=p("string", "Absolute path to a file to read and search. Provide this or text."),
        k=p("integer", "Number of results to return (default 5)", minimum=1, maximum=20, default=5),
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
        "**Purpose**: Semantically search for relevant passages in a given text or file without reading the entire document. Pass either text or path (not both).\n\n"
        "**When to use**:\n"
        "- 大概知道想要什么方向，但不确定具体关键词，用 grep 不知道搜什么的时候\n"
        "- 比如新接手代码库想找「处理超时的逻辑」但不知道函数名叫什么\n"
        "- 比如长文档里找「和安全性相关的部分」，安全可能写成了防护、鉴权、加密\n\n"
        "**When NOT to use**:\n"
        "- 已经知道具体关键词 → 用 grep\n"
        "- 想从头到尾读文件 → 用 read_file\n\n"
        "**Query examples**:\n"
        "  search_text(query='error handling timeout logic', path='/src/main.py')\n"
        "  search_text(query='authentication encryption security', text='...')\n"
        "  search_text(query='configuration setup initialization', path='/app/config.py')\n\n"
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
