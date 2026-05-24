from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from .filesystem_base import _FsTool, _normalize_quotes, _line_tag
from nanobot.agent.tools import file_state

@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(old_text))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """Locate all exact substring matches of old_text in content."""
    return _find_exact_matches(content, old_text)


def _find_match_line_numbers(content: str, old_text: str) -> list[int]:
    """Return 1-based starting line numbers for exact matches of old_text."""
    return [match.line for match in _find_matches(content, old_text)]


def _collapse_internal_whitespace(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _diagnose_near_match(old_text: str, actual_text: str) -> list[str]:
    """Return actionable hints describing why text was close but not exact."""
    hints: list[str] = []

    if old_text.lower() == actual_text.lower() and old_text != actual_text:
        hints.append("letter case differs")
    if _collapse_internal_whitespace(old_text) == _collapse_internal_whitespace(actual_text) and old_text != actual_text:
        hints.append("whitespace differs")
    if old_text.rstrip("\n") == actual_text.rstrip("\n") and old_text != actual_text:
        hints.append("trailing newline differs")
    if _normalize_quotes(old_text) == _normalize_quotes(actual_text) and old_text != actual_text:
        hints.append("quote style differs")

    return hints


def _first_lines_window(old_text: str, content: str) -> tuple[list[str], list[str]]:
    """Return the first line-window for diagnostics without fuzzy matching."""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    start = 0
    window_lines = lines[:window] if len(lines) >= window else lines

    actual_text = "".join(window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return window_lines, hints


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """Locate old_text in content with exact substring match only.

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    matches = _find_matches(content, old_text)
    if not matches:
        return None, 0
    return matches[0].text, len(matches)


@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a file to edit. Directories and special files are rejected."),
        old_text=p("string", "Text to find and replace. Must match EXACTLY and be UNIQUE in the file — include surrounding lines for disambiguation, or set replace_all=true. "
            "Leave empty (or omit) to prepend new_text at file beginning. Pair with first_line+last_line for line-range mode instead of text matching."),
        new_text=p("string", "Replacement text for old_text. Pass empty string to delete old_text. "
            "When used with first_line+last_line (no old_text), replaces the entire line range with this text."),
        replace_all=p("boolean",
            "Replace all occurrences (default false). "
            "When old_text appears multiple times and replace_all=false, "
            "the tool returns a warning listing the line numbers of each match. "
            "If you only want to replace one specific occurrence, add more surrounding "
            "context to old_text to make it unique.",
            default=False,
        ),
        first_line=p("integer", "Line number to start replacing from (1-indexed). When set with last_line, replaces that line range with new_text directly — no fuzzy matching needed.",
            minimum=1,
        ),
        last_line=p("integer", "Line number to end replacing at (1-indexed, inclusive). Must be >= first_line.",
            minimum=1,
        ),
        line_tag=p("string",
            "Tag from read_file output for the line at first_line (e.g. 'Q8fA'). "
            "When set with first_line+last_line, the tool verifies the tag matches "
            "the actual file content before editing. Prevents editing the wrong line "
            "when the file has changed since you read it."
        ),
        then_grep=p("string",
            "If set, searches the edited file for this exact substring (not a regex) after saving, "
            "and returns matching line numbers and content. "
            "Helps verify the edit landed correctly."
        ),
        required=["path", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text (exact substring match only)."""

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    name = "edit_file"

    description = (
        "**用途**: 通过文本匹配或行号范围修改文件内容。\n\n"
        "**两种模式**:\n"
        "- **old_text/new_text**（默认）— 精确文本匹配替换，适合小段替换\n"
        "- **first_line/last_line**（行号范围）— 按行号替换，支持传入 read_file 输出的 line_tag 做校验\n\n"
        "**行号 + line_tag**:\n"
        "read_file 输出格式为 `LINENO:TAG| CONTENT`（如 `42:Q8fA| def foo():`）。\n"
        "编辑时传入 `first_line=42, line_tag='Q8fA'`，工具会自动校验该行内容是否已被修改。\n"
        "推荐用这种方式编辑已知行号范围的内容，避免输出大段 old_text。\n\n"
        "**自动验证**:\n"
        "编辑后会自动搜索 new_text 的第一行内容是否写入成功。\n"
        "也可用 `then_grep` 参数指定一个字符串来验证写入结果。\n\n"
        "**限制**:\n"
        "- 不支持跨文件的查找替换\n"
        "- 文本匹配容忍空格差异，但不能跨越函数/类重组\n\n"
        "**错误应对**:\n"
        "- old_text 找不到 → 显示 diff 辅助定位\n"
        "- old_text 出现多次且 replace_all=false → 显示每处匹配的行号，要求提供更多上下文或设置 replace_all=true\n"
        "- line_tag 不匹配 → 说明文件已改变，要求重新 read_file\n"
        "- 文件不存在 → 返回错误\n\n"
        "**边界条件**:\n"
        "- 需要创建新文件 → 用 write_file\n"
        "- 需要跨文件查找替换 → 用 exec sed\n\n"
        "**极简案例**: edit_file(path='main.py', first_line=42, last_line=45, line_tag='Q8fA', new_text='def bar():')\n"
        "→ 替换文件中 42-45 行为新内容"
    )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """Strip trailing whitespace from each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        first_line: int | None = None,
        last_line: int | None = None,
        line_tag: str | None = None,
        then_grep: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if new_text is None:
                raise ValueError("Unknown new_text")

            # Line-based mode: replace lines first_line through last_line
            if first_line is not None or last_line is not None:
                if first_line is None or last_line is None:
                    raise ValueError("Both first_line and last_line must be provided (or neither)")
                result = await self._edit_by_lines(path, new_text, first_line, last_line, line_tag=line_tag)
                if result.startswith("Successfully"):
                    fp = self._resolve(path)
                    # Auto-verify: use then_grep if provided, otherwise first line of new_text
                    verify_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
                    if then_grep:
                        result += f"\n{self._find_in_file(fp, then_grep)}"
                    elif verify_lines:
                        vr = self._find_in_file(fp, verify_lines[0], max_matches=3)
                        if vr:
                            result += f"\nVerified:\n{vr}"
                return result

            if old_text is None:
                raise ValueError("Unknown old_text")

            # .ipynb detection
            if path.endswith(".ipynb"):
                return "Error: This is a Jupyter notebook. Use the notebook_edit tool instead of edit_file."

            fp = self._resolve(path)

            # Create-file semantics: old_text='' + file doesn't exist → create
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    file_state.record_write(fp)
                    msg = f"Successfully created {fp.as_posix()}"
                    verify_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
                    if verify_lines:
                        vr = self._find_in_file(fp, verify_lines[0], max_matches=3)
                        if vr:
                            msg += f"\nVerified:\n{vr}"
                    if then_grep:
                        msg += f"\n{self._find_in_file(fp, then_grep)}"
                    return msg
                return self._file_not_found_msg(path, fp)

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            # Create-file: old_text='' but file exists and not empty → reject
            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return f"Error: Cannot create file — {path} already exists and is not empty."
                fp.write_text(new_text, encoding="utf-8")
                file_state.record_write(fp)
                msg = f"Successfully edited {fp.as_posix()}"
                verify_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
                if verify_lines:
                    vr = self._find_in_file(fp, verify_lines[0], max_matches=3)
                    if vr:
                        msg += f"\nVerified:\n{vr}"
                elif then_grep:
                    msg += f"\n{self._find_in_file(fp, then_grep)}"
                return msg

            # Read-before-edit check
            warning = file_state.check_read(fp)

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if count > 1 and not replace_all:
                line_numbers = [match.line for match in matches]
                preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                if len(line_numbers) > 3:
                    preview += ", ..."
                location_hint = f" at {preview}" if preview else ""
                return (
                    f"Warning: old_text appears {count} times{location_hint}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")

            # Trailing whitespace stripping (skip markdown to preserve double-space line breaks)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            selected = matches if replace_all else matches[:1]
            new_content = content
            for match in reversed(selected):
                replacement = norm_new

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing
                # newline to avoid leaving a blank line
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            file_state.record_write(fp)
            msg = f"Successfully edited {fp.as_posix()}"

            # Auto-verify: check new_text landed in the file
            verify_lines = [l.strip() for l in norm_new.splitlines() if l.strip()]
            verify_result = ""
            if verify_lines:
                verify_pattern = verify_lines[0]
                verify_result = self._find_in_file(fp, verify_pattern, max_matches=3)
            if warning:
                msg = f"{warning}\n{msg}"
            if verify_result:
                msg += f"\nVerified:\n{verify_result}"
            if then_grep:
                msg += f"\n{self._find_in_file(fp, then_grep)}"
            return msg
        except PermissionError as e:
            logger.warning("EditFile permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("EditFile failed: {}", e)
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """Build an error message for file-not-found."""
        return f"Error: File not found: {path}"

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_window_lines, hints = _first_lines_window(old_text, content)
        if best_window_lines:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, first lines)",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nShowing file content for comparison:\n{diff}"
            )

        if hints:
            return (
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."

    async def _edit_by_lines(
        self, path: str, new_text: str, first_line: int, last_line: int,
        line_tag: str | None = None,
    ) -> str:
        """Replace lines first_line through last_line (1-indexed) with new_text.

        When *line_tag* is provided, verifies that the tag of the existing
        content at *first_line* matches before editing.
        """
        fp = self._resolve(path)
        if not fp.exists():
            return self._file_not_found_msg(path, fp)
        if path.endswith(".ipynb"):
            return "Error: This is a Jupyter notebook. Use the notebook_edit tool instead of edit_file."

        warning = file_state.check_read(fp)
        raw = fp.read_bytes()
        uses_crlf = b"\r\n" in raw
        content = raw.decode("utf-8").replace("\r\n", "\n")
        lines = content.split("\n")

        # 1-indexed → 0-indexed
        start_idx = first_line - 1
        end_idx = last_line - 1

        if start_idx < 0:
            return f"Error: first_line must be >= 1, got {first_line}"
        if end_idx < start_idx:
            return f"Error: last_line ({last_line}) must be >= first_line ({first_line})"
        if end_idx >= len(lines):
            return f"Error: last_line ({last_line}) exceeds file length ({len(lines)} lines)"

        # Verify line tag if provided
        if line_tag:
            actual_tag = _line_tag(lines[start_idx])
            if line_tag != actual_tag:
                return (
                    f"Error: line tag mismatch at line {first_line}. "
                    f"Expected tag '{line_tag}', got '{actual_tag}'. "
                    f"The file content has changed since you read it. "
                    f"Use read_file to get the current content and tags."
                )

        norm_new = new_text.replace("\r\n", "\n")
        # Strip trailing whitespace (skip markdown)
        if fp.suffix.lower() not in self._MARKDOWN_EXTS:
            norm_new = self._strip_trailing_ws(norm_new)
        norm_new = norm_new.rstrip("\n")

        # Replace the line range
        new_lines = lines[:start_idx] + [norm_new] + lines[end_idx + 1:]
        new_content = "\n".join(new_lines)
        if uses_crlf:
            new_content = new_content.replace("\n", "\r\n")

        fp.write_bytes(new_content.encode("utf-8"))
        file_state.record_write(fp)
        msg = f"Successfully edited {fp.as_posix()} (replaced lines {first_line}-{last_line})"
        if warning:
            msg = f"{warning}\n{msg}"
        return msg


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

