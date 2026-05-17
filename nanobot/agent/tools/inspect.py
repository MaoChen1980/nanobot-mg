"""inspect_text tool — preview document structure without LLM.

DEPRECATED: Use read_file(mode='overview') instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.tools._section_utils import detect_sections, format_section_overview
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema

_MAX_TEXT_BYTES = 5 * 1024 * 1024


@tool_parameters(
    tool_parameters_schema(
        text=p("string", "Text content to inspect (max 5 MB). Provide this or path."),
        path=p("string", "File path to inspect — file. Relative to workspace root. Absolute paths also accepted. Provide this or text."),
        max_sections=p("integer", "Maximum number of sections to return (default 10)", minimum=1, maximum=20),
        mode=p("string", "Detection mode: 'auto' (default, structure first → semantic fallback), 'semantic' (embedding-based segmentation), 'structure' (headings/separators only)", enum=["auto", "semantic", "structure"]),
    ),
    required=[],
)
class InspectTextTool(Tool):
    """Preview a document's structure before reading it in full."""

    name = "inspect_text"
    read_only = True

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    description = (
        "**用途**: 预览文档结构（类目录），text 和 path 二选一传入。\n\n"
        "**注意**: 此工具已弃用，请使用 read_file(mode='overview') 替代。\n\n"
        "**限制**:\n"
        "- 输入最大 5 MB\n"
        "- 段落检测是启发式的，可能过度拆分或合并\n"
        "- 关键词基于词频，不是语义\n\n"
        "**错误应对**:\n"
        "- 返回空/单段落 → 语义模式需要 sentence-transformers 依赖\n"
        "- 依赖未安装 → 自动降级到换行拆分（精度降低）\n\n"
        "**边界条件**:\n"
        "- 已知文档内容 → 直接读，不预览\n"
        "- 需要按语义搜索 → 用 search_text\n"
        "- 需要关键词匹配 → 用 grep\n\n"
        "**极简案例**: read_file(path='long_doc.md', mode='overview')\n"
        "→ 返回各章节 heading + 关键词 + 偏移量"
    )

    async def execute(
        self, text: str | None = None, path: str | None = None,
        max_sections: int = 10, mode: str = "auto", **kwargs: Any,
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

        if not text or not text.strip():
            return "Empty text."

        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            max_mb = _MAX_TEXT_BYTES // (1024 * 1024)
            return (
                f"text too large ({len(text)} chars / "
                f"{len(text.encode('utf-8'))} bytes). "
                f"Maximum is {max_mb} MB."
            )

        sections = detect_sections(text, max_sections, mode)
        lines = text.split("\n")
        return format_section_overview(sections, len(text), len(lines))
