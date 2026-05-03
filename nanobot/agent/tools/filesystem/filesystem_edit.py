import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from .filesystem_base import _FsTool, _normalize_quotes, _preserve_quote_style, _reindent_like_match
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


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    old_lines = old_text.splitlines()
    if not old_lines:
        return []

    content_lines = content.splitlines()
    content_lines_keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []

    offsets: list[int] = []
    pos = 0
    for line in content_lines_keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    if normalize_quotes:
        stripped_old = [_normalize_quotes(line.strip()) for line in old_lines]
    else:
        stripped_old = [line.strip() for line in old_lines]

    matches: list[_MatchSpan] = []
    window_size = len(stripped_old)
    for i in range(len(content_lines) - window_size + 1):
        window = content_lines[i : i + window_size]
        if normalize_quotes:
            comparable = [_normalize_quotes(line.strip()) for line in window]
        else:
            comparable = [line.strip() for line in window]
        if comparable != stripped_old:
            continue

        start = offsets[i]
        end = offsets[i + window_size]
        if content_lines_keepends[i + window_size - 1].endswith("\n"):
            end -= 1
        matches.append(
            _MatchSpan(
                start=start,
                end=end,
                text=content[start:end],
                line=i + 1,
            )
        )
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    norm_content = _normalize_quotes(content)
    norm_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = norm_content.find(norm_old, start)
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
        start = idx + max(1, len(norm_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """Locate all matches using progressively looser strategies."""
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _find_match_line_numbers(content: str, old_text: str) -> list[int]:
    """Return 1-based starting line numbers for the current matching strategies."""
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


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    """Find the closest line-window match and return ratio/start/snippet/hints."""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    best_ratio, best_start = -1.0, 0
    best_window_lines: list[str] = []

    for i in range(max(1, len(lines) - window + 1)):
        current = lines[i : i + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
            best_window_lines = current

    actual_text = "".join(best_window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return best_ratio, best_start, best_window_lines, hints


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """Locate old_text in content with a multi-level fallback chain:

    1. Exact substring match
    2. Line-trimmed sliding window (handles indentation differences)
    3. Smart quote normalization (curly ↔ straight quotes)

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    matches = _find_matches(content, old_text)
    if not matches:
        return None, 0
    return matches[0].text, len(matches)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        first_line=IntegerSchema(
            None,
            description="Line number to start replacing from (1-indexed). When set with last_line, replaces that line range with new_text directly — no fuzzy matching needed.",
            minimum=1,
        ),
        last_line=IntegerSchema(
            None,
            description="Line number to end replacing at (1-indexed, inclusive). Must be >= first_line.",
            minimum=1,
        ),
        required=["path", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file. Two modes:\n"
            "1. Text-matching (default): replace old_text with new_text. "
            "Tolerates minor whitespace/indentation differences and curly/straight quote mismatches. "
            "If old_text matches multiple times, provide more context or set replace_all=true.\n"
            "2. Line-based: set first_line and last_line to replace lines N through M directly. "
            "Fastest for simple edits — no text matching needed. "
            "Shows a diff of the closest match on failure."
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
                return await self._edit_by_lines(path, new_text, first_line, last_line)

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
                    return f"Successfully created {fp}"
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
                return f"Successfully edited {fp}"

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
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

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
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            return msg
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """Build an error message with 'Did you mean ...?' suggestions."""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return "\n".join(parts)

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
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

        norm_new = new_text.replace("\r\n", "\n")
        # Strip trailing whitespace (skip markdown)
        if fp.suffix.lower() not in self._MARKDOWN_EXTS:
            norm_new = self._strip_trailing_ws(norm_new)

        # Replace the line range
        new_lines = lines[:start_idx] + [norm_new] + lines[end_idx + 1:]
        new_content = "\n".join(new_lines)
        if uses_crlf:
            new_content = new_content.replace("\n", "\r\n")

        fp.write_bytes(new_content.encode("utf-8"))
        file_state.record_write(fp)
        msg = f"Successfully edited {fp} (replaced lines {first_line}-{last_line})"
        if warning:
            msg = f"{warning}\n{msg}"
        return msg


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

