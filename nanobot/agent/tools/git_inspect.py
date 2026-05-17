"""Git inspection tool — see what changed, who changed it, and why."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool


@tool_parameters(
    tool_parameters_schema(
        path=p("string", "File or directory path to filter commits by — relative to repository root (e.g. 'src/main.py'), NOT an absolute filesystem path. Git pathspec only, use forward slashes."),
        since=p("string", "Time range, e.g. '7 days ago', '2024-01-01', '1 month'"),
        commit=p("string", "Specific commit SHA to inspect in detail"),
        max_commits=p("integer", "Maximum commits in log view (default 10, max 50)", minimum=1, maximum=50, default=10),
    ),
    required=[],
)
class GitInspectTool(_FsTool):
    """See what changed in the git repository — commit messages, authors, diffs."""

    name = "git_inspect"
    read_only = True

    description = (
        "**用途**: 查看 git 历史 — 谁改了什么、为什么改。\n\n"
        "**核心价值**: 输出自带结构化摘要（commit 数、作者数、文件变更统计），"
        "一目了然改动范围和影响，不用逐条读 commit。"
        "exec(git log) 做不到这一点。\n\n"
        "**限制**:\n"
        "- 最多返回 50 个 commit\n"
        "- git 查询超时 30 秒\n\n"
        "**错误应对**:\n"
        "- 不在 git 仓库 → 返回错误\n"
        "- commit SHA 无效 → 返回提示\n\n"
        "**边界条件**:\n"
        "- 无参数 → 显示最近 10 个 commit\n"
        "- 传 path → 过滤该文件的 commit\n"
        "- 传 commit → 显示该 commit 完整 diff\n\n"
        "**极简案例**: git_inspect(path='src/main.py', since='7 days ago')\n"
        "→ 返回 7 天内修改过 src/main.py 的 commits"
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

            if path and (path.startswith("/") or re.match(r"[A-Za-z]:[\\/]", path)):
                return (
                    f"Error: path must be a git pathspec relative to repository root "
                    f"(e.g. 'src/main.py'), got absolute path: {path!r}. "
                    f"Do NOT pass absolute filesystem paths — use repo-relative paths with forward slashes."
                )

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

        summary = self._build_log_summary(git_dir, since, path, commits)

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
            lines.append("")  # blank line after title

        for c in commits:
            date = c["date"][:10]
            sha = c["sha"][:8]
            subject = c["subject"][:80]
            lines.append(f"  {sha}  {date}  {c['author']}  {subject}")

        lines.append("")
        lines.append(f"({len(commits)} commits shown)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Structured summary
    # ------------------------------------------------------------------

    def _build_log_summary(
        self, git_dir: Path, since: str | None, path: str | None,
        commits: list[dict[str, str]],
    ) -> str | None:
        """Build structured summary: commit count, authors, file change stats."""
        if not commits:
            return None

        authors = set(c["author"] for c in commits)
        parts: list[str] = [
            f"- **{len(commits)} commits**, **{len(authors)} authors**",
        ]

        # Aggregate file stats via shortstat (one line per commit)
        cmd = [
            "git", "-C", str(git_dir), "log",
            f"--max-count={len(commits)}",
            "--shortstat", "--format=",
        ]
        if since:
            cmd.append(f"--since={since}")
        if path:
            cmd.extend(["--", path])

        result = self._run(cmd)
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
