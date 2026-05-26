from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools._section_utils import detect_sections, format_section_overview
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from .filesystem_base import _FsTool, _is_blocked_device, _line_tag, _parse_page_range
from nanobot.agent.tools import file_state
from nanobot.utils.media_decode import build_image_content_blocks, detect_image_mime

@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a file to read. Supports text files, PDFs (pages param), and images (rendered as Markdown)."),
        mode=p("string", "Reading mode: 'full' (outputs numbered lines) or 'overview' (previews structure via headings/sections without reading the whole file). Use overview when unsure what a file contains.",
            enum=["full", "overview"], default="full",
        ),
        extract=p("string", "Optional Python regex (re.compile). Applied after offset/limit — only matching lines are returned, with 1 line of context before/after each match. Use instead of grep+cat for filtering logs or code. Example: 'Error.*timeout'."),
        offset=p("integer", "Line number to start reading from (1-indexed)",
            minimum=1, default=1,
        ),
        limit=p("integer", "Maximum number of lines to read",
            minimum=1, default=2000,
        ),
        pages=p("string", "Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    name = "read_file"

    description = (
        "**用途**: 读取文件内容（文本/图片/PDF/Office），支持模式选择、正则过滤、按行分段。\n\n"
        "**输出格式 (mode=full)**:\n"
        "每行格式为 `LINENO:4CHAR_TAG| CONTENT` (e.g. `42:Q8fA| def foo():`)。\n"
        "其中 TAG 是内容的 4 字符校验码，可直接传给 edit_file 的 line_tag 参数做修改校验。\n\n"
        "**模式**:\n"
        "- `mode=full`（默认）— 完整读取，支持 offset+limit 分页\n"
        "- `mode=overview` — 只看结构不读全文（heading/section 预览），不想读完整文件时用\n"
        "- `extract=正则` — 只返回匹配行+前后各 1 行上下文，替代 grep+cat 组合\n\n"
        "**什么时候用**:\n"
        "- 需要查看文件内容时\n"
        "- 不确定文件中有什么 → 先 `mode=overview` 预览结构\n"
        "- 需要按 offset+limit 分段读取大文件时\n"
        "- 需要从 PDF 或 Office 文档提取文本时\n"
        "- 需要用正则提取匹配的行时\n\n"
        "**什么时候不用**:\n"
        "- 只需要文件名/目录结构 → 用 list_dir\n"
        "- 需要搜索文件内容 → 用 grep\n"
        "- 需要批量读多个文件 → 用 read_files\n"
        "- 需要读取 URL → 用 web_fetch\n"
        "- 需要修改文件 → 用 edit_file 或 write_file\n"
    )

    read_only = True

    async def execute(self, path: str = "", mode: str = "full", extract: str | None = None, offset: int = 1, limit: int | None = None, pages: str | None = None, **kwargs: Any) -> Any:
        try:
            # Device path blacklist
            if _is_blocked_device(path):
                return f"Error: Reading {path} is blocked (device path that could hang or produce infinite output)."

            fp = self._resolve(path)
            if _is_blocked_device(fp):
                return f"Error: Reading {fp.as_posix()} is blocked (device path that could hang or produce infinite output)."
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            # PDF support
            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            # Office document support
            if fp.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                return self._read_office_doc(fp)

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            # Read dedup: same path + offset + limit + unchanged mtime + unchanged hash → stub
            entry = file_state._default_manager._state.get(str(fp.resolve()))
            try:
                current_mtime = os.path.getmtime(fp)
            except OSError:
                current_mtime = 0.0
            if entry and entry.can_dedup and entry.offset == offset and entry.limit == limit:
                if current_mtime == entry.mtime:
                    current_hash = file_state._hash_file(str(fp))
                    if current_hash == entry.content_hash:
                        return f"[File unchanged since last read: {path}]"

            # Read the file content (no dedup or file changed)
            raw = fp.read_bytes()
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file - return error message
                mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                if mime and mime.startswith("image/"):
                    return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            # Normalize CRLF -> LF before line-splitting. Primarily a Windows
            # concern (git checkouts with autocrlf, editors saving CRLF) but
            # applied on all platforms so downstream StrReplace/Grep behavior
            # is consistent regardless of where the file was written.
            text_content = text_content.replace("\r\n", "\n")

            # -- overview mode: preview structure without reading the full file --
            if mode == "overview":
                sections = detect_sections(text_content, max_sections=10)
                lines = text_content.split("\n")
                return format_section_overview(sections, len(text_content), len(lines))

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}:{_line_tag(line)}| {line}" for i, line in enumerate(all_lines[start:end])]

            # -- extract mode: filter to lines matching the regex + 1 line context --
            if extract:
                try:
                    extract_re = re.compile(extract)
                except re.error as e:
                    return f"Error: invalid extract regex: {e}"
                match_idx: set[int] = set()
                for i, numbered_line in enumerate(numbered):
                    content = numbered_line.split("|", 1)[1] if "|" in numbered_line else numbered_line
                    if extract_re.search(content):
                        if i > 0:
                            match_idx.add(i - 1)
                        match_idx.add(i)
                        if i + 1 < len(numbered):
                            match_idx.add(i + 1)
                if match_idx:
                    numbered = [numbered[i] for i in sorted(match_idx)]
                else:
                    return f"(No lines matched extract pattern: {extract})"

            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            file_state.record_read(fp, offset=offset, limit=limit)
            return result
        except PermissionError as e:
            logger.warning("ReadFile permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("ReadFile failed: {}", e)
            return f"Error reading file: {e}"

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        try:
            import fitz  # pymupdf
        except ImportError:
            return "Error: PDF reading requires pymupdf. Install with: pip install pymupdf"

        try:
            doc = fitz.open(str(fp))
        except Exception as e:
            logger.warning("Failed to read PDF: {}", e)
            return f"Error reading PDF: {e}"

        total_pages = len(doc)
        if pages:
            try:
                start, end = _parse_page_range(pages, total_pages)
            except (ValueError, IndexError):
                doc.close()
                return f"Error: Invalid page range '{pages}'. Use format like '1-5'."
            if start > end or start >= total_pages:
                doc.close()
                return f"Error: Page range '{pages}' is out of bounds (document has {total_pages} pages)."
        else:
            start = 0
            end = min(total_pages - 1, self._MAX_PDF_PAGES - 1)

        if end - start + 1 > self._MAX_PDF_PAGES:
            end = start + self._MAX_PDF_PAGES - 1

        parts: list[str] = []
        for i in range(start, end + 1):
            page = doc[i]
            text = page.get_text().strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()

        if not parts:
            return f"(PDF has no extractable text: {fp.as_posix()})"

        result = "\n\n".join(parts)
        if end < total_pages - 1:
            result += f"\n\n(Showing pages {start + 1}-{end + 1} of {total_pages}. Use pages='{end + 2}-{min(end + 1 + self._MAX_PDF_PAGES, total_pages)}' to continue.)"
        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(PDF text truncated at ~256K chars)"
        return result

    def _read_office_doc(self, fp: Path) -> str:
        from nanobot.utils.document import extract_text

        result = extract_text(fp)

        if result is None:
            return f"Error: Unsupported file format: {fp.suffix}"

        if result.startswith("[error:"):
            return f"Error reading {fp.suffix.upper()} file: {result}"

        if not result:
            return f"({fp.suffix.upper().lstrip('.')} has no extractable text: {fp.as_posix()})"

        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(Document text truncated at ~256K chars)"

        return result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


