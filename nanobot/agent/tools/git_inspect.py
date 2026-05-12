"""Git inspection tool — see what changed, who changed it, and why."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool


@tool_parameters(
    tool_parameters_schema(
        path=p("string", "File or directory path to filter commits by"),
        since=p("string", "Time range, e.g. '7 days ago', '2024-01-01', '1 month'"),
        commit=p("string", "Specific commit SHA to inspect in detail"),
        max_commits=p("integer", "Maximum commits in log view (default 10, max 50)", minimum=1, maximum=50),
    ),
    required=[],
)
class GitInspectTool(_FsTool):
    """See what changed in the git repository — commit messages, authors, diffs."""

    name = "git_inspect"
    read_only = True

    description = (
        "Inspect git history — who changed what and why.\n\n"
        "Without arguments: shows recent commits.\n"
        "With `path`: filter commits touching that file/directory.\n"
        "With `since`: filter by time range.\n"
        "With `commit`: show full commit details including diff.\n\n"
        "Use this when:\n"
        "- You need to understand why a change was made\n"
        "- You're debugging and want to see recent changes to a file\n"
        "- You want to find who modified something and when\n\n"
        "All arguments are optional — calling with no args shows the last 10 commits. "
        "Max 50 commits per query; 30s timeout."
    )

    async def execute(
        self,
        path: str | None = None,
        since: str | None = None,
        commit: str | None = None,
        max_commits: int = 10,
        **kwargs: Any,
    ) -> str:
        try:
            workspace = self._resolve(".")
            git_dir = self._find_git_root(workspace)
            if not git_dir:
                return "Error: Not a git repository (or any parent directory)"

            if commit:
                return self._show_commit(git_dir, commit)

            return self._show_log(git_dir, since, path, max_commits)

        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error inspecting git: {e}"

    @staticmethod
    def _find_git_root(start: Path) -> Path | None:
        current = start.resolve()
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return None

    # ------------------------------------------------------------------
    # Log view
    # ------------------------------------------------------------------

    def _show_log(self, git_dir: Path, since: str | None, path: str | None, max_commits: int) -> str:
        cmd = [
            "git", "-C", str(git_dir),
            "log",
            f"--max-count={max_commits}",
            "--format=COMMIT%n%H%n%an%n%ae%n%ai%n%s%n%b%n---",
        ]
        if since:
            cmd.append(f"--since={since}")
        if path:
            cmd.extend(["--", path])

        result = self._run(cmd)
        if result is None:
            return "Error: git log failed"

        commits = self._parse_log(result)
        if not commits:
            filters = []
            if since:
                filters.append(f"since={since}")
            if path:
                filters.append(f"path={path}")
            filter_str = f" ({', '.join(filters)})" if filters else ""
            return f"(No commits found{filter_str})"

        lines: list[str] = [f"# Git log — {git_dir.name}"]
        if since:
            lines.append(f"Since: {since}")
        if path:
            lines.append(f"Path: {path}")
        lines.append("")

        for c in commits:
            date = c["date"][:10]
            sha = c["sha"][:8]
            subject = c["subject"][:80]
            lines.append(f"  {sha}  {date}  {c['author']}  {subject}")

        lines.append("")
        lines.append(f"({len(commits)} commits shown)")
        return "\n".join(lines)

    @staticmethod
    def _parse_log(output: str) -> list[dict[str, str]]:
        commits: list[dict[str, str]] = []
        blocks = output.split("\n---\n")
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            parts = block.split("\n", 6)
            if len(parts) < 6:
                continue
            # parts[0] is the "COMMIT" marker, skip it
            commits.append({
                "sha": parts[1],
                "author": parts[2],
                "email": parts[3],
                "date": parts[4],
                "subject": parts[5],
                "body": parts[6] if len(parts) > 6 else "",
            })
        return commits

    # ------------------------------------------------------------------
    # Single commit view
    # ------------------------------------------------------------------

    def _show_commit(self, git_dir: Path, sha: str) -> str:
        cmd = [
            "git", "-C", str(git_dir), "show",
            "--format=%H%n%an%n%ae%n%ai%n%s%n%b",
            "--stat",
            "--patch",
            sha,
        ]
        result = self._run(cmd)
        if result is None:
            return f"Error: commit {sha} not found"

        return f"# Commit {sha}\n\n{result}"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _run(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

        if proc.returncode != 0:
            return None
        return proc.stdout.strip()
