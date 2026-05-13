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
        "**用途**: 在 memory/ 目录中按语义搜索相关段落，不是关键词匹配。\n\n"
        "**限制**:\n"
        "- 只搜索 memory/ 目录下的 .md 文件\n"
        "- 新建或修改的内容不会立刻出现，需要等索引重建\n"
        "- 每次最多返回 20 条结果（k ≤ 20）\n"
        "- 不适合代码搜索（embedding 模型对自然语言优化）\n"
        "- 语义搜索 ≈ 模糊匹配，可能漏掉特定术语\n\n"
        "**错误应对**:\n"
        "- 返回空 []: 可能原因 (a) 无匹配 → 换 query 重试 (b) 依赖未安装 → "
        "该工具总是返回空，换用 grep\n"
        "- 结果不相关: 换 query 表述方式，用完整句子而非关键词\n\n"
        "**边界条件**:\n"
        "- 你知道精确关键词 → 用 grep\n"
        "- 你知道文件路径 → 直接用 read_file\n"
        "- 只需要在一段文本内搜索 → 用 search_text\n"
        "- 想看文档结构 → 用 inspect_text\n\n"
        "**极简案例**:\n"
        "  search_memory(query='memory consolidation bug', k=5)\n"
        "  → 返回带 score/source/heading/行号的匹配段落列表"
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
        "**用途**: 在一段文本中按语义搜索相关段落，不需要全文阅读。"
        "text 和 path 二选一传入。\n\n"
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
        "- 不知道文档内容 → 先用 inspect_text 看结构\n"
        "- 需要在 memory/ 目录搜索 → 用 search_memory\n"
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
