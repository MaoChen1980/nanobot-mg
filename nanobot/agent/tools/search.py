"""Search tools: grep_tool and glob_tool."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from loguru import logger

from nanobot.agent.tools.filesystem.filesystem import _FsTool

_DEFAULT_HEAD_LIMIT = 250
T = TypeVar("T")
_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "sh": ("*.sh", "*.bash"),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
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


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


class _SearchTool(_FsTool):
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    def _display_path(self, target: Path, root: Path) -> str:
        """Always return an absolute resolved path for unambiguous cross-tool use."""
        return target.resolve().as_posix()

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            for filename in sorted(filenames):
                yield current / filename

    def _iter_entries(
        self,
        root: Path,
        *,
        include_files: bool,
        include_dirs: bool,
    ) -> Iterable[Path]:
        if root.is_file():
            if include_files:
                yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            if include_dirs:
                for dirname in dirnames:
                    yield current / dirname
            if include_files:
                for filename in sorted(filenames):
                    yield current / filename


class GlobTool(_SearchTool):
    """Find files matching a glob pattern."""

    @property
    def name(self) -> str:
        return "glob_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Search for files matching a glob pattern by filename.\n\n"
            "**When to use**:\n"
            "- When you need to find files by glob pattern matching their filenames\n"
            "- To list directory contents: `pattern=\"*\"` for top-level, `pattern=\"**/*\"` for recursive, `entry_type=\"both\"` for files+dirs\n"
            "- Default max 250 results (up to 1000)\n\n"
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match, e.g. '*.py' or 'tests/**/test_*.py'. Use '*' for top-level list, '**/*' for recursive.",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to a directory to search in. **Required.**",
                    "minLength": 1,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Legacy alias for head_limit",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "default": 250,
                    "description": "Maximum number of matches to return (default 250). Set to 0 for unlimited results.",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Skip the first N matching entries before returning results",
                    "minimum": 0,
                    "maximum": 100000,
                },
                "entry_type": {
                    "type": "string",
                    "default": "files",
                    "enum": ["files", "dirs", "both"],
                    "description": "Whether to match files, directories, or both (default files)",
                },
            },
            "required": ["pattern", "path"],
        }

    async def execute(
        self,
        pattern: str,
        path: str = "",
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        entry_type: str = "files",
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                return "Error: `path` is required — provide an absolute path."
            root = self._resolve(path)
            if not root.exists():
                return f"Error: Path not found: {path} — use glob_tool to locate it first"
            if not root.is_dir():
                return f"Error: Not a directory: {path}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            include_files = entry_type in {"files", "both"}
            include_dirs = entry_type in {"dirs", "both"}
            matches: list[tuple[str, float]] = []
            for entry in self._iter_entries(
                root,
                include_files=include_files,
                include_dirs=include_dirs,
            ):
                rel_path = entry.relative_to(root).as_posix()
                if _match_glob(rel_path, entry.name, pattern):
                    display = self._display_path(entry, root)
                    if entry.is_dir():
                        display += "/"
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    matches.append((display, mtime))

            if not matches:
                return f"No paths matched pattern '{pattern}' in {path}"

            matches.sort(key=lambda item: (-item[1], item[0]))
            ordered = [name for name, _ in matches]
            paged, truncated = _paginate(ordered, limit, offset)
            result = "\n".join(paged)
            if note := _pagination_note(limit, offset, truncated):
                result += f"\n\n{note}"
            return result
        except PermissionError as e:
            logger.warning("Glob permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("Glob failed: {}", e)
            return f"Error finding files: {e}"


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _MAX_RESULT_CHARS = 256_000
    _MAX_FILE_BYTES = 10_000_000

    @property
    def name(self) -> str:
        return "grep_tool"

    @property
    def description(self) -> str:
        return (
            "**Purpose**: Search file contents using a regex pattern.\n\n"
            "**Output format (output_mode)**:\n"
            "- `content`:  PATH:LINENO header + \"> LINENO| matched line\" + \"  LINENO| context line\"\n"
            "  The matched line's path and lineno are directly usable as read_file path and offset params\n"
            "- `files_with_matches` (default): one absolute path per line\n"
            "- `count`:  path:match count\n\n"
            "**When to use**:\n"
            "- When you need to search file contents for lines matching a pattern\n"
            "- Supports regular expressions\n\n"
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for (default). Set fixed_strings=true to treat as plain text instead.",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to a file or directory to search in. **Required.**",
                    "minLength": 1,
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional file type shorthand (legacy alias: type). Values: py/python, js, ts, tsx, jsx, json, md/markdown, go, rs/rust, java, sh, yaml/yml, toml, sql, html, css",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "default": False,
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "default": "files_with_matches",
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "default": 0,
                    "description": "Lines of context before each match, max 50 (only applies when output_mode='content')",
                    "minimum": 0,
                    "maximum": 50,
                },
                "context_after": {
                    "type": "integer",
                    "default": 0,
                    "description": "Lines of context after each match, max 50 (only applies when output_mode='content')",
                    "minimum": 0,
                    "maximum": 50,
                },
                "max_matches": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in content mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in files_with_matches or count mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "default": 250,
                    "description": "Maximum number of results to return. In content mode this limits matching line blocks; in other modes it limits file entries.",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
            "required": ["pattern", "path"],
        }

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str],
        match_line: int,
        before: int,
        after: int,
    ) -> str:
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    @staticmethod
    async def _try_rg_search(
        pattern: str,
        target: Path,
        *,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        glob: str | None = None,
        context_before: int = 0,
        context_after: int = 0,
    ) -> str | None:
        """Try ripgrep first. Returns formatted result or None for fallback."""
        rg_path = shutil.which("rg")
        if not rg_path:
            return None

        args = [rg_path, "--no-ignore", "--color", "never"]
        if case_insensitive:
            args.append("-i")
        if fixed_strings:
            args.append("-F")
        if glob:
            args.extend(["-g", glob])
        if context_before:
            args.extend(["-B", str(context_before)])
        if context_after:
            args.extend(["-A", str(context_after)])

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "content":
            args.append("-n")
        # count mode handled below (rg -c format differs)

        args.extend(["--", pattern, str(target)])

        if output_mode == "count":
            # rg -c outputs "path:count" per file — use -c with --count-matches
            count_args = args[:-2] + ["-c", "--count-matches", "--", pattern, str(target)]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *count_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except Exception:
                return None
            if proc.returncode not in (0, 1):
                return None
            text = stdout.decode("utf-8", errors="replace").strip()
            if not text:
                return f"No matches found for pattern '{pattern}'"
            # Normalize "path:count" lines
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            return "\n".join(lines)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            return None
        if proc.returncode not in (0, 1):
            return None

        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return f"No matches found for pattern '{pattern}'"

        if output_mode == "files_with_matches":
            return text

        # content mode: normalize rg "path:line:content" → "path:lineno| content"
        # to match the existing nanobot grep output format
        lines = text.split("\n")
        normalized: list[str] = []
        for ln in lines:
            if not ln.strip():
                continue
            # rg with -n outputs: path:lineno:content
            # We want:           path:lineno| content
            colon = ln.find(":")
            if colon == -1:
                normalized.append(ln)
                continue
            rest = ln[colon + 1:]
            # Check if it's a "path:lineno:content" match or a context line
            # Context lines from -A/-B don't have lineno prefix
            second_colon = rest.find(":")
            if second_colon != -1 and rest[:second_colon].isdigit():
                lineno = rest[:second_colon]
                content = rest[second_colon + 1:]
                normalized.append(f"{ln[:colon]}:{lineno}| {content}")
            else:
                # Context line — preserve raw
                normalized.append(ln)
        return "\n".join(normalized)

    async def execute(
        self,
        pattern: str,
        path: str = "",
        glob: str | None = None,
        file_type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        # Backwards compat: legacy alias "type"
        if file_type is None and kwargs.get("type"):
            file_type = kwargs["type"]
        if not path:
            return "Error: `path` is required — provide an absolute path."
        try:
            target = self._resolve(path)
            if not target.exists():
                return f"Error: Path not found: {path} — use glob_tool to locate it first"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            # Try ripgrep first for directory searches (fast path).
            # Falls back to Python implementation when rg is unavailable,
            # targeting a single file, or using legacy-only features.
            if target.is_dir() and not file_type:
                rg_result = await self._try_rg_search(
                    pattern, target,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                    output_mode=output_mode,
                    glob=glob,
                    context_before=context_before,
                    context_after=context_after,
                )
                if rg_result is not None:
                    if rg_result.startswith("No matches"):
                        return rg_result
                    # Apply offset/limit post-hoc for consistent pagination
                    lines = rg_result.split("\n")
                    paged, truncated = _paginate(lines, head_limit or _DEFAULT_HEAD_LIMIT, offset)
                    result = "\n".join(paged)
                    if note := _pagination_note(head_limit or _DEFAULT_HEAD_LIMIT, offset, truncated):
                        result += f"\n\n{note}"
                    return result

            flags = re.IGNORECASE if case_insensitive else 0
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as e:
                return f"Error: invalid regex pattern: {e}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            blocks: list[str] = []
            result_chars = 0
            seen_content_matches = 0
            truncated = False
            size_truncated = False
            skipped_binary = 0
            skipped_large = 0
            matching_files: list[str] = []
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}
            root = target if target.is_dir() else target.parent

            for file_path in self._iter_files(target):
                rel_path = file_path.relative_to(root).as_posix()
                if glob and not _match_glob(rel_path, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, file_type):
                    continue

                raw = file_path.read_bytes()
                if len(raw) > self._MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue

                lines = content.splitlines()
                display_path = self._display_path(file_path, root)
                file_had_match = False
                for idx, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    file_had_match = True

                    if output_mode == "count":
                        counts[display_path] = counts.get(display_path, 0) + 1
                        continue
                    if output_mode == "files_with_matches":
                        if display_path not in matching_files:
                            matching_files.append(display_path)
                            file_mtimes[display_path] = mtime
                        break

                    seen_content_matches += 1
                    if seen_content_matches <= offset:
                        continue
                    if limit is not None and len(blocks) >= limit:
                        truncated = True
                        break
                    block = self._format_block(
                        display_path,
                        lines,
                        idx,
                        context_before,
                        context_after,
                    )
                    extra_sep = 2 if blocks else 0
                    if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                        size_truncated = True
                        break
                    blocks.append(block)
                    result_chars += extra_sep + len(block)
                if output_mode == "count" and file_had_match:
                    if display_path not in matching_files:
                        matching_files.append(display_path)
                        file_mtimes[display_path] = mtime
                if output_mode in {"count", "files_with_matches"} and file_had_match:
                    continue
                if truncated or size_truncated:
                    break

            if output_mode == "files_with_matches":
                if not matching_files:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    paged, truncated = _paginate(ordered_files, limit, offset)
                    result = "\n".join(paged)
            elif output_mode == "count":
                if not counts:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    ordered, truncated = _paginate(ordered_files, limit, offset)
                    lines = [f"{name}: {counts[name]}" for name in ordered]
                    result = "\n".join(lines)
            else:
                if not blocks:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and blocks:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and counts:
                notes.append(
                    f"(total matches: {sum(counts.values())} in {len(counts)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as e:
            logger.warning("Grep permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("Grep failed: {}", e)
            return f"Error searching files: {e}"
