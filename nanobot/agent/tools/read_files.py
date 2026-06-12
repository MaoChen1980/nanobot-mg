"""Batch read multiple files matching a glob pattern — one call instead of glob+read loop."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from nanobot.agent.tools import file_state
from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool

_IGNORE_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"})


@tool_parameters(
    build_parameters_schema(
        pattern=p("string", "Glob pattern to match files, e.g. 'src/**/*.py' or 'tests/*.py'. Can also be an absolute path (e.g. '/path/to/dir/*.py') — path is auto-extracted. (legacy alias: glob)"),
        extract=p("string", "Optional regex — only lines matching this pattern are returned from each file (with 1 line context). Legacy alias: grep"),
        path=p("string", "Absolute path to a directory to search from. Optional if pattern is already an absolute path (path is auto-extracted from pattern)."),
        max_files=p("integer", "Maximum number of files to read (default 10, max 50)", minimum=1, maximum=50, default=10),
        max_lines=p("integer", "Maximum lines per file (default 2000, max 5000). Most files fit within 2000 lines — increase if file is longer.",
            minimum=1, maximum=5000, default=2000),
        required=["pattern"],
    ),
)
class ReadFilesTool(_FsTool):
    """Read multiple files at once by glob pattern — no more glob→read loops."""

    name = "read_files_tool"
    read_only = True
    _MAX_FILE_BYTES = 10_000_000

    description = (
        "**Purpose**: Batch read multiple files matching a glob pattern — one call replaces glob+read loops.\n\n"
        "**When to use**:\n"
        "- When you need to read multiple files matching a pattern simultaneously\n"
        "- When you need to search for keywords across multiple files and view matched lines with context (pass extract param)\n\n"
    )

    async def execute(
        self,
        pattern: str = "",
        extract: str | None = None,
        path: str = "",
        max_files: int = 10,
        max_lines: int = 2000,
        **kwargs: Any,
    ) -> str:
        # Backwards compat: legacy aliases "glob" and "grep"
        if not pattern and kwargs.get("glob"):
            pattern = kwargs["glob"]
        if extract is None and kwargs.get("grep"):
            extract = kwargs["grep"]
        if not path:
            # Auto-extract path from pattern if it's an absolute path
            abs_pattern = pattern.strip().replace("\\", "/")
            if ":" in abs_pattern or abs_pattern.startswith("/"):
                p = Path(abs_pattern)
                parent = p.parent
                if parent.exists() and parent.is_dir():
                    path = str(parent)
                    pattern = p.name
            if not path:
                return "Error: `path` is required — provide an absolute path to a directory."
        try:
            root = self._resolve(path)
            if not root.exists():
                return f"Error: Path not found: {path}"
            if not root.is_dir():
                return f"Error: Not a directory: {path}"

            matches: list[tuple[Path, float]] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
                for filename in sorted(filenames):
                    fp = Path(dirpath) / filename
                    rel = fp.relative_to(root).as_posix()
                    if _match_glob(rel, filename, pattern):
                        try:
                            mtime = fp.stat().st_mtime
                        except OSError:
                            mtime = 0.0
                        matches.append((fp, mtime))

            if not matches:
                return f"No files matched glob pattern: {pattern}"

            matches.sort(key=lambda x: (-x[1], x[0]))
            selected = matches[:max_files]

            extract_re = re.compile(extract) if extract else None

            parts: list[str] = []
            total_chars = 0
            skipped_binary = 0
            skipped_large = 0

            for fp, _ in selected:
                raw = fp.read_bytes()
                if len(raw) > self._MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue
                file_state.record_read(fp)
                text = text.replace("\r\n", "\n")
                lines = text.split("\n")
                rel_path = fp.relative_to(root).as_posix()

                if extract_re:
                    match_idx: set[int] = set()
                    for i, line in enumerate(lines):
                        if extract_re.search(line):
                            if i > 0:
                                match_idx.add(i - 1)
                            match_idx.add(i)
                            if i + 1 < len(lines):
                                match_idx.add(i + 1)
                    if not match_idx:
                        continue
                    shown = [lines[i] for i in sorted(match_idx) if i < len(lines)]
                    shown = shown[:max_lines]
                else:
                    shown = lines[:max_lines]

                header = f"═══ {rel_path} ═══"
                content = "\n".join(f"{i + 1}| {l}" for i, l in enumerate(shown))
                block = f"{header}\n{content}"
                if len(lines) > max_lines:
                    block += f"\n(... {len(lines) - max_lines} more lines. Increase max_lines to read more.)"

                extra_sep = 2 if parts else 0
                if total_chars + extra_sep + len(block) > 128_000:
                    parts.append("(output truncated due to size)")
                    break
                parts.append(block)
                total_chars += extra_sep + len(block)

            if not parts:
                return f"No readable text files matched: {pattern}"

            result = "\n\n".join(parts)

            if len(selected) < len(matches):
                result += f"\n\n(Showing {len(selected)} of {len(matches)} matching files. Increase max_files to read more.)"
            notes: list[str] = []
            if skipped_binary:
                notes.append(f"Skipped {skipped_binary} binary/unreadable files")
            if skipped_large:
                notes.append(f"Skipped {skipped_large} files >2 MB")
            if notes:
                result += "\n\n" + "\n".join(notes)

            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading files: {e}"


# -- helpers (ported from search.py to avoid circular deps) --------------------


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = pattern.strip().replace("\\", "/")
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2
