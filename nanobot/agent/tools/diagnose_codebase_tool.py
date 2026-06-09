"""Diagnose tool — grep codebase + git history to root-cause errors."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool


@tool_parameters(
    build_parameters_schema(
        error=p("string", "Error message, keyword, or code to investigate"),
        path=p("string", "Absolute path to a file or directory to narrow the search scope. Required for code search — omit to get git history only."),
        max_results=p("integer", "Max grep results to show (default 20)", minimum=1, maximum=50, default=20),
        days=p("integer", "Days of git history to check (default 7)", minimum=1, maximum=90, default=7),
    ),
    required=["error"],
)
class DiagnoseTool(_FsTool):
    """Diagnose errors by searching code and checking recent changes."""

    name = "diagnose_codebase_tool"
    read_only = True

    description = (
        "**Purpose**: Investigate error root causes in one shot by combining code search and git history.\n\n"
        "**When to use**:\n"
        "- You encounter an error (e.g. TypeError) and need to find both relevant code locations and recent git commits\n"
        "- Pass the full error message — keywords are automatically extracted and searched across code + git history\n\n"
    )

    async def execute(
        self,
        error: str = "",
        path: str | None = None,
        max_results: int = 20,
        days: int = 7,
        **kwargs: Any,
    ) -> str:
        try:
            workspace = self._workspace or Path.cwd().resolve()
            git_dir = self._find_git_root(workspace)
        except PermissionError:
            git_dir = None

        terms = self._extract_terms(error)
        parts = [f"# Diagnosis: {error[:80]}", ""]

        hits = self._search_code(terms, path, max_results)
        if hits:
            parts.append(f"## Code matches ({len(hits)} lines)")
            parts.append("")
            parts.extend(hits[:max_results])
        elif path:
            parts.append("## Code matches")
            parts.append("(No direct code matches found)")
        else:
            parts.append("## Code matches")
            parts.append("(No path specified — pass `path` to search code)")
        parts.append("")

        if git_dir:
            since = f"{days} days ago"
            log = await self._git_log(git_dir, terms, since, 10)
            if log:
                parts.append(f"## Recent changes (last {days} days, {len(log)} commits)")
                parts.append("")
                parts.extend(log)
            else:
                parts.append(f"## Recent changes")
                parts.append(f"(No commits touching related files in the last {days} days)")
        else:
            parts.append("## Git history")
            parts.append("(Not a git repository)")

        parts.append("")
        summary_parts: list[str] = []
        if hits:
            summary_parts.append(f"found {len(hits)} code matches")
        if git_dir and log:
            summary_parts.append(f"{len(log)} recent commits")
        summary = ", ".join(summary_parts) if summary_parts else "no relevant results"
        parts.append(f"---\n({summary})")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_code(self, terms: list[str], path: str | None, max_results: int) -> list[str]:
        if not terms:
            return []
        if not path:
            return []
        seen: set[str] = set()
        hits: list[str] = []
        try:
            search_root = self._resolve(path)
        except (ValueError, PermissionError):
            return []
        term = terms[0]

        try:
            matches = self._grep_files(term, str(search_root))
            for f in matches[:max_results]:
                key = f.lower()
                if key not in seen:
                    seen.add(key)
                    if len(hits) < max_results:
                        first_match = self._first_match_line(f, term)
                        rel = Path(f).as_posix()
                        hits.append(f"  {rel}{first_match}")
        except OSError:
            pass
        return hits

    @staticmethod
    def _grep_files(term: str, search_root: str) -> list[str]:
        """Pure-Python grep: return sorted file paths containing term."""
        _EXTENSIONS = frozenset({".py", ".js", ".ts", ".go", ".rs", ".java", ".kt"})
        root = Path(search_root).resolve()
        if not root.is_dir():
            return []
        matches: list[str] = []
        for path in root.rglob("*"):
            if path.suffix not in _EXTENSIONS or path.is_dir() or path.name.startswith("."):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                if term in text:
                    matches.append(str(path))
            except (OSError, UnicodeDecodeError):
                continue
        return matches

    @staticmethod
    def _first_match_line(filepath: str, term: str) -> str:
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if term in line:
                    return f"  L{lineno}: {line.strip()[:120]}"
        except Exception:
            return ""
        return ""

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    @staticmethod
    def _find_git_root(start: Path) -> Path | None:
        current = start.resolve()
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return None

    @staticmethod
    async def _git_log(git_dir: Path, terms: list[str], since: str, max_count: int) -> list[str]:
        if not terms:
            return []
        log_lines: list[str] = []
        seen_shas: set[str] = set()
        term = terms[0]

        try:
            cmd = [
                "git", "-C", str(git_dir), "log",
                f"--since={since}", f"--max-count={max_count}",
                "--format=%h | %ai | %s",
                f"--grep={term}", "-i", "--all",
            ]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                for line in proc.stdout.strip().split("\n"):
                    sha = line.split(" | ")[0] if " | " in line else line[:8]
                    if sha not in seen_shas:
                        seen_shas.add(sha)
                        log_lines.append(f"  {line}")
        except (subprocess.TimeoutExpired, OSError):
            pass

        return log_lines[:max_count]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_terms(error: str) -> list[str]:
        text = error.strip().strip('"').strip("'").strip("`")
        first_line = text.split("\n")[0][:200]
        return [first_line] if first_line else []
