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
from nanobot.agent.tools.schema import p, tool_parameters_schema

_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Tool 2: search_text
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        query=p("string", "Natural-language query to find relevant passages"),
        text=p("string", "Text content to search within (max 5 MB). Provide this or path."),
        path=p("string", "File path to read and search — file. Relative to workspace root. Absolute paths also accepted. Provide this or text."),
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
        "**用途**: 在给定文本或文件中按语义搜索相关段落，不需要全文阅读。"
        "text 和 path 二选一传入。"
        "与 recall(mode='knowledge') 的区别：search_text 搜的是你传入的文本/文件，"
        "recall 搜的是 memory/ 目录（持久化记忆）。\n\n"
        "**限制**:\n"
        "- 输入最大 5 MB\n"
        "- 每次最多返回 20 条结果（k ≤ 20）\n"
        "- 不适合代码搜索\n"
        "- 语义 ≈ 模糊匹配，不保证找到所有提及\n"
        "- 文本在 chunk 边界可能被切分\n\n"
        "**错误应对**:\n"
        "- 返回空 []: (a) 无匹配 → 换 query (b) 依赖未安装 → 一直返回空，"
        "换用 grep (c) 输入空 → 检查输入\n"
        "- 'Provide either text or path': 必须提供其中一个且只能一个\n"
        "- 'text too large': 输入超 5 MB，截短或用 grep\n\n"
        "**边界条件**:\n"
        "- 需要精确关键词匹配 → 用 grep\n"
        "- 不知道文档内容 → 用 read_file(mode='overview')\n"
        "- 需要在 memory/ 目录搜索 → 用 recall(mode='knowledge')\n"
        "- 代码搜索 → 用 grep\n\n"
        "**极简案例**:\n"
        "  search_text(query='退款流程', text=long_article)\n"
        "  → 返回带 score/offset/length 的相关段落列表"
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
