"""Git inspection tool — see what changed, who changed it, and why."""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool


@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a file or directory inside a git repository. Tool locates the repo and shows relevant commit history."),
        since=p("string", "Time range, e.g. '7 days ago', '2024-01-01', '1 month'"),
        commit=p("string", "Specific commit SHA to inspect in detail (shows full diff)"),
        max_commits=p("integer", "Maximum commits in log view (default 10, max 50)", minimum=1, maximum=50, default=10),
    ),
    required=["path"],
)
class GitInspectTool(_FsTool):
    """See what changed in the git repository — commit messages, authors, diffs."""

    name = "git_inspect"
    read_only = True

    description = (
        "**Purpose**: Inspect git history — who changed what and why.\n\n"
        "**Usage**:\n"
        "- **Log only** (overview) → omit `commit` param, returns a list of commits\n"
        "- **Full diff** (in-depth) → pass `commit=SHA`, shows the complete diff for that commit\n\n"
        "**When to use**:\n"
        "- When you need to review recent commits for a file or directory\n"
        "- When you need to examine the specific changes (diff) in a commit\n\n"
    )

    async def execute(
        self,
        path: str,
        since: str | None = None,
        commit: str | None = None,
        max_commits: int = 10,
        **kwargs: Any,
    ) -> str:
        try:
            path = path.replace("\\", "/")
            abs_path = Path(path).resolve()
            search_dir = abs_path if abs_path.is_dir() else abs_path.parent
            git_dir = self._find_git_root(search_dir)

            if not git_dir:
                return "Error: Not a git repository (or any parent directory)"

            resolved_path: str | None = None
            if abs_path != git_dir.resolve():
                try:
                    resolved_path = abs_path.relative_to(git_dir.resolve()).as_posix()
                except ValueError:
                    pass

            if commit:
                return await self._show_commit(git_dir, commit)

            return await self._show_log(git_dir, since, resolved_path, max_commits)

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

    async def _show_log(self, git_dir: Path, since: str | None, path: str | None, max_commits: int) -> str:
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

        result, err = await self._run(cmd)
        if result is None:
            return f"Error: git log failed — {err}" if err else "Error: git log failed"

        commits = self._parse_log(result)
        if not commits:
            filters = []
            if since:
                filters.append(f"since={since}")
            if path:
                filters.append(f"path={path}")
            filter_str = f" ({', '.join(filters)})" if filters else ""
            return f"(No commits found{filter_str})"

        summary = await self._build_log_summary(git_dir, since, path, commits)

        lines: list[str] = [f"# Git log — {git_dir.name}"]
        if summary:
            lines.append("")
            lines.append("**Summary:**")
            lines.append(summary)
            lines.append("")
        if since:
            lines.append(f"Since: {since}")
        if path:
            lines.append(f"Path: {path}")
        if not summary and not since and not path:
            lines.append("")

        for c in commits:
            sha = c["sha"][:8]
            date = c["date"][:10]
            subject = c["subject"][:80]
            body = c.get("body", "").strip()
            line = f"  {sha}  {date}  {c['author']}  {subject}"
            if body:
                first_body = body.split("\n")[0][:60]
                if first_body and first_body != subject:
                    line += f"\n           {first_body}"
            lines.append(line)

        lines.append("")
        lines.append(f"({len(commits)} commits shown)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Structured summary
    # ------------------------------------------------------------------

    async def _build_log_summary(
        self, git_dir: Path, since: str | None, path: str | None,
        commits: list[dict[str, str]],
    ) -> str | None:
        if not commits:
            return None

        authors = set(c["author"] for c in commits)
        parts: list[str] = [
            f"- **{len(commits)} commits**, **{len(authors)} authors**",
        ]

        cmd = [
            "git", "-C", str(git_dir), "log",
            f"--max-count={len(commits)}",
            "--shortstat", "--format=",
        ]
        if since:
            cmd.append(f"--since={since}")
        if path:
            cmd.extend(["--", path])

        result, err = await self._run(cmd)
        if result:
            total_files = total_added = total_removed = 0
            for line in result.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = re.search(r"(\d+) file", line)
                if m:
                    total_files += int(m.group(1))
                m = re.search(r"(\d+) insertion", line)
                if m:
                    total_added += int(m.group(1))
                m = re.search(r"(\d+) deletion", line)
                if m:
                    total_removed += int(m.group(1))
            if total_files:
                parts.append(
                    f"- **{total_files} files** changed, "
                    f"**+{total_added}/-{total_removed}**"
                )

        return "\n".join(parts)

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

    async def _show_commit(self, git_dir: Path, sha: str) -> str:
        cmd = [
            "git", "-C", str(git_dir), "show",
            "--format=%H%n%an%n%ae%n%ai%n%s%n%b",
            "--stat",
            "--patch",
            sha,
        ]
        result, err = await self._run(cmd)
        if result is None:
            msg = err or f"commit {sha} not found"
            return f"Error: {msg}"

        return f"# Commit {sha}\n\n{result}"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    async def _run(cmd: list[str]) -> tuple[str | None, str | None]:
        """Returns (stdout, error). error is None on success."""
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except (FileNotFoundError, OSError) as e:
            return None, str(e)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            return None, stderr or f"exit code {proc.returncode}"
        return proc.stdout.strip(), None
