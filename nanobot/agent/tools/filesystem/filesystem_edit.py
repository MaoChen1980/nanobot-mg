from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools import file_state
from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.danger import danger_warning
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.utils.compat import dataclass
from .filesystem_base import _FsTool, _normalize_quotes


_EDIT_FILE_SCHEMA = build_parameters_schema(
    path=p("string", "Absolute path to a file to edit. Directories and special files are rejected."),
    old_text=p("string", "Text to find and replace. Must match EXACTLY and be UNIQUE in the file — include surrounding lines for disambiguation, or set replace_all=true. "
        "Leave empty to create a new file (errors if file already exists with content). "
        "Pair with first_line+last_line for line-range mode instead of text matching."),
    new_text=p("string", "REQUIRED in all modes. Replacement text. "
        "Pass empty string to delete old_text. "
        "When used with first_line+last_line (no old_text), replaces the entire line range.",
        minLength=0),
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
    then_grep=p("string",
        "If set, searches the edited file for this exact substring (not a regex) after saving, "
        "and returns matching line numbers and content. "
        "Helps verify the edit landed correctly."
    ),
    danger_override=p("boolean",
        "When true, bypasses danger detection for edits that remove large amounts of content. "
        "Use only after verifying the edit is safe. "
        "Default: false. Detection re-enables automatically for the next call.",
        default=False,
    ),
    required=["path", "new_text"],
)


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


@tool_parameters(_EDIT_FILE_SCHEMA)
class EditFileTool(_FsTool):
    """Edit a file by replacing text (exact substring match only)."""

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    name = "edit_file_tool"

    description = (
        "**Purpose**: Modify file content via text matching or line number ranges.\n\n"
        "**Prerequisite — Read the file first**:\n"
        "You MUST read a file with read_file_tool before editing it. edit_file_tool checks whether the file\n"
        "was read; if not, it returns a warning asking you to read first. This ensures you have the latest\n"
        "content and can provide correct old_text or line numbers. Reading also lets the system detect\n"
        "concurrent modifications via SHA256 hash verification.\n\n"
        "**Two Modes**:\n"
        "- **old_text/new_text** (default) — exact text match replacement, suitable for small replacements\n"
        "- **first_line/last_line** (line number range) — replace by line numbers, just pass the line numbers\n\n"
        "**Auto Protection**:\n"
        "Checks if the file was modified externally after reading (SHA256 full-file hash).\n"
        "If the file has changed, the tool includes a warning in the result (does not affect edit execution).\n\n"
        "**Limitations**:\n"
        "- Does not support cross-file find-and-replace\n"
        "- Text matching is exact (no fuzzy or whitespace-tolerant matching)\n\n"
        "**Error Handling**:\n"
        "- old_text not found → shows diff to help locate\n"
        "- old_text appears multiple times and replace_all=false → shows line numbers for each match\n"
        "- File does not exist → returns error\n\n"
        "**Minimal Example**: edit_file_tool(path='main.py', first_line=42, last_line=45, new_text='def bar():')\n"
        "→ First use read_file_tool to read the file, then call edit_file_tool with the correct line numbers"
    )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """Strip trailing whitespace from each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self,
        path: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        first_line: int | None = None,
        last_line: int | None = None,
        then_grep: str | None = None,
        danger_override: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if new_text is None:
                raise ValueError("Unknown new_text")

            # Danger detection: warn when removing large content
            if not danger_override and old_text and new_text == "" and len(old_text) > 200:
                return danger_warning(
                    problem=f"Removing {len(old_text)} characters from {path}",
                    risk="Large content removal may delete more than intended, "
                         "especially if old_text matches unexpected locations",
                    suggestion="Back up the file first (git commit or save_stage_tool), then read it "
                               "with read_file_tool to verify the exact text you want to remove "
                               "before editing",
                    tool_name="edit_file_tool",
                )

            # Line-based mode: replace lines first_line through last_line
            if first_line is not None or last_line is not None:
                if first_line is None or last_line is None:
                    raise ValueError("Both first_line and last_line must be provided (or neither)")
                result = await self._edit_by_lines(path, new_text, first_line, last_line)
                if result.startswith("Successfully"):
                    fp = self._resolve(path)
                    verify_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
                    if then_grep:
                        result += f"\n{self._find_in_file(fp, then_grep)}"
                    elif verify_lines:
                        vr = self._verify_write(fp, verify_lines[0])
                        if vr:
                            result += f"\n{vr}"
                return result

            if old_text is None:
                return "Error: old_text is required in text-match mode. Omit old_text only when using first_line+last_line for line-range replacement."

            # .ipynb detection
            if path.endswith(".ipynb"):
                return "Error: This is a Jupyter notebook. Use the notebook_edit_tool instead of edit_file_tool."

            fp = self._resolve(path)

            # Create-file semantics: old_text='' + file doesn't exist -> create
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    file_state.record_write(fp)
                    msg = f"Successfully created {fp.as_posix()}"
                    verify_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
                    if verify_lines:
                        vr = self._verify_write(fp, verify_lines[0])
                        if vr:
                            msg += f"\n{vr}"
                    if then_grep:
                        msg += f"\n{self._find_in_file(fp, then_grep)}"
                    return msg
                return self._file_not_found_msg(path, fp)

            # Read-before-edit enforcement: must read file before editing
            # Skip for create-file semantics (old_text="") — model isn't editing content
            if old_text is not None and old_text != "":
                read_warning = file_state.check_read(fp)
                if read_warning:
                    return read_warning

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            # Create-file: old_text='' but file exists and not empty -> reject
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
                    vr = self._verify_write(fp, verify_lines[0])
                    if vr:
                        msg += f"\n{vr}"
                elif then_grep:
                    msg += f"\n{self._find_in_file(fp, then_grep)}"
                return msg

            # Staleness warning (informational -- does not block edit)
            hash_warning = file_state.check_content_hash(fp)

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

            # Trailing whitespace stripping (skip markdown)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            selected = matches if replace_all else matches[:1]
            new_content = content
            for match in reversed(selected):
                replacement = norm_new

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing newline
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            file_state.record_write(fp)
            msg = f"Successfully edited {fp.as_posix()}"

            # Auto-verify: pattern check + syntax check for Python files
            verify_lines = [l.strip() for l in norm_new.splitlines() if l.strip()]
            verify_result = ""
            if verify_lines:
                verify_result = self._verify_write(fp, verify_lines[0])
            if hash_warning:
                msg = f"{hash_warning}\n{msg}"
            if verify_result:
                msg += f"\n{verify_result}"
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
    ) -> str:
        """Replace lines first_line through last_line (1-indexed) with new_text."""
        fp = self._resolve(path)
        if not fp.exists():
            return self._file_not_found_msg(path, fp)

        # Read-before-edit enforcement
        read_warning = file_state.check_read(fp)
        if read_warning:
            return read_warning

        if path.endswith(".ipynb"):
            return "Error: This is a Jupyter notebook. Use the notebook_edit_tool instead of edit_file_tool."

        # Staleness warning (informational -- does not block edit)
        hash_warning = file_state.check_content_hash(fp)

        raw = fp.read_bytes()
        uses_crlf = b"\r\n" in raw
        content = raw.decode("utf-8").replace("\r\n", "\n")
        has_trailing_newline = content.endswith("\n")
        lines = content.splitlines()

        # 1-indexed -> 0-indexed
        start_idx = first_line - 1
        end_idx = last_line - 1

        if start_idx < 0:
            return f"Error: first_line must be >= 1, got {first_line}"
        if end_idx < start_idx:
            return f"Error: last_line ({last_line}) must be >= first_line ({first_line})"
        if start_idx >= len(lines) or end_idx >= len(lines):
            return f"Error: last_line ({last_line}) exceeds file length ({len(lines)} lines)"

        norm_new = new_text.replace("\r\n", "\n")
        if fp.suffix.lower() not in self._MARKDOWN_EXTS:
            norm_new = self._strip_trailing_ws(norm_new)
        norm_new = norm_new.rstrip("\n")

        new_lines = lines[:start_idx] + [norm_new] + lines[end_idx + 1:]
        new_content = "\n".join(new_lines)
        if has_trailing_newline:
            new_content += "\n"
        if uses_crlf:
            new_content = new_content.replace("\n", "\r\n")

        fp.write_bytes(new_content.encode("utf-8"))
        file_state.record_write(fp)
        msg = f"Successfully edited {fp.as_posix()} (replaced lines {first_line}-{last_line})"
        if hash_warning:
            msg = f"{hash_warning}\n{msg}"
        return msg
