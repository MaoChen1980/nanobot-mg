"""Git inspection tool — browse version history and changes.

Pure Python implementation using dulwich (no system git required).
Use to view commit history, inspect diffs, and track file evolution
in any git-managed directory.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from dulwich.diff_tree import tree_changes
from dulwich.patch import write_object_diff
from dulwich.repo import Repo

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.is_dir() or git_dir.is_file():
            return current
        current = current.parent
    return None


def _parse_since(since: str) -> float | None:
    s = since.strip().lower()
    now = time.time()

    m = re.match(r"(\d+)\s*(day|days|week|weeks|month|months|year|years)\s+ago", s)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        multipliers = {
            "day": 86400, "days": 86400,
            "week": 604800, "weeks": 604800,
            "month": 2592000, "months": 2592000,
            "year": 31536000, "years": 31536000,
        }
        return now - num * multipliers[unit]

    try:
        dt = datetime.strptime(s.split(" ")[0], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        pass

    return None


def _commit_touches_path(repo, commit, rel_path: str) -> bool:
    if not commit.parents:
        return True
    parent = repo[commit.parents[0]]
    if parent.type_name != b"commit":
        return True
    try:
        path_b = rel_path.replace("\\", "/").encode()
        for change in tree_changes(repo.object_store, parent.tree, commit.tree):
            for obj in (change.old, change.new):
                if obj is not None and obj.path and path_b in obj.path:
                    return True
    except (KeyError, ValueError):
        pass
    return False


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Absolute path to a file or directory inside a git repository. "
            "The tool finds the repository root and shows version history "
            "relevant to this path.",
        ),
        "sha": p(
            "string",
            "Optional commit SHA to inspect in detail. "
            "When provided, shows the full diff (what changed) for that version. "
            "Omit to see the commit log.",
        ),
        "since": p(
            "string",
            "Optional time filter, e.g. '7 days ago', '2024-01-01', '1 month'. "
            "Only shows commits after this time.",
        ),
        "max_commits": p(
            "integer",
            "Maximum number of commits to show (default 20, max 50).",
            minimum=1,
            maximum=50,
            default=20,
        ),
    },
    required=["path"],
)
class GitInspectTool(Tool):
    """Browse version history and inspect changes in a git-managed directory.

    Two modes:
    - **Log mode** (default): show a list of saved versions with SHA, author,
      timestamp, and message.  Optionally filter by path or time range.
    - **Diff mode** (pass ``sha``): show the exact changes (unified diff) for
      a specific version.

    Works with any git repository — including directories versioned by the
    ``checkpoint`` tool.  Pure Python implementation, no system git required.
    """

    name = "git_inspect"
    read_only = True

    async def execute(
        self,
        path: str,
        sha: str | None = None,
        since: str | None = None,
        max_commits: int = 20,
        **kwargs: Any,
    ) -> str:
        path = path.replace("\\", "/")
        abs_path = Path(path).resolve()
        git_root = _find_git_root(abs_path)
        if not git_root:
            return (
                "Error: No git repository found. "
                "Use `checkpoint(path, message)` first to initialize version "
                "control in this directory."
            )

        try:
            rel_path: str | None = None
            if abs_path != git_root:
                rel_path = abs_path.relative_to(git_root).as_posix()
        except ValueError:
            rel_path = None

        with Repo(str(git_root)) as repo:
            if sha:
                return self._show_diff(repo, sha)
            return self._show_log(repo, since, rel_path, max_commits)

    # ------------------------------------------------------------------
    # Log view
    # ------------------------------------------------------------------

    def _show_log(
        self, repo: Repo, since: str | None, rel_path: str | None,
        max_commits: int,
    ) -> str:
        since_ts = _parse_since(since) if since else None

        try:
            head = repo.refs[b"HEAD"]
        except KeyError:
            return "No version history — no commits found in this repository."

        entries: list[dict[str, Any]] = []
        sha: bytes | None = head
        while sha and len(entries) < max_commits:
            commit = repo[sha]
            if commit.type_name != b"commit":
                break

            if since_ts is not None and commit.commit_time < since_ts:
                sha = commit.parents[0] if commit.parents else None
                continue

            if rel_path and not _commit_touches_path(repo, commit, rel_path):
                sha = commit.parents[0] if commit.parents else None
                continue

            msg = commit.message.decode("utf-8", errors="replace").strip()
            author = commit.author.decode("utf-8", errors="replace")
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit.commit_time))
            entries.append({
                "sha": sha.hex()[:8],
                "author": author,
                "timestamp": ts,
                "message": msg,
                "commit_time": commit.commit_time,
            })
            sha = commit.parents[0] if commit.parents else None

        if not entries:
            return "No matching commits found."

        summary = self._build_summary(repo, entries)

        lines = [f"# Version history — {Path(repo.path).name}"]
        if summary:
            lines.append("")
            lines.append(summary)
        if since:
            lines.append("")
            lines.append(f"Since: {since}")
        if rel_path:
            lines.append(f"Path: {rel_path}")
        lines.append("")

        for e in entries:
            lines.append(f"  {e['sha']}  {e['timestamp']}  {e['author']}  {e['message']}")

        lines.append("")
        lines.append(f"({len(entries)} commits shown)")
        return "\n".join(lines)

    def _build_summary(
        self, repo: Repo, entries: list[dict[str, Any]],
    ) -> str | None:
        authors = set(e["author"] for e in entries)
        parts: list[str] = [f"- **{len(entries)} commits**, **{len(authors)} authors**"]

        total_files: set[str] = set()
        for entry in entries:
            walk = repo.refs[b"HEAD"]
            target_sha = entry["sha"]
            while walk:
                commit = repo[walk]
                if commit.type_name != b"commit":
                    break
                if walk.hex()[:8] == target_sha:
                    break
                walk = commit.parents[0] if commit.parents else None
            else:
                continue

            if not commit.parents:
                continue

            try:
                parent = repo[commit.parents[0]]
                for change in tree_changes(repo.object_store, parent.tree, commit.tree):
                    for obj in (change.old, change.new):
                        if obj is not None and obj.path:
                            total_files.add(obj.path.decode())
            except (KeyError, ValueError):
                pass

        if total_files:
            parts.append(f"- **{len(total_files)} files** changed")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Diff view
    # ------------------------------------------------------------------

    def _show_diff(self, repo: Repo, sha: str) -> str:
        full_sha = self._resolve_sha(repo, sha)
        if not full_sha:
            return (
                f"Error: Version '{sha}' not found. "
                "Use `git_inspect` to list available commits."
            )

        commit = repo[full_sha]
        if commit.type_name != b"commit":
            return f"Error: '{sha}' is not a valid commit."

        msg = commit.message.decode("utf-8", errors="replace").strip()
        author = commit.author.decode("utf-8", errors="replace")
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit.commit_time))

        if not commit.parents:
            tree = repo[commit.tree]
            files = [entry.path.decode() for entry in tree.iteritems()]
            file_list = "\n".join(f"  A  {f}" for f in files) if files else "  (empty)"
            return (
                f"# Commit {sha[:8]}  —  Initial version\n\n"
                f"Author: {author}\n"
                f"Date:   {ts}\n\n"
                f"{msg}\n\n"
                f"[Root commit — all files added]\n{file_list}"
            )

        buf = BytesIO()
        parent = repo[commit.parents[0]]
        try:
            for change in tree_changes(repo.object_store, parent.tree, commit.tree):
                write_object_diff(buf, repo.object_store, change.old, change.new)
        except (KeyError, ValueError) as e:
            return f"Error generating diff: {e}"

        diff_text = buf.getvalue().decode("utf-8", errors="replace")
        if not diff_text.strip():
            diff_text = "  (no file changes — metadata only)"

        return (
            f"# Commit {sha[:8]}  —  Changes\n\n"
            f"Author: {author}\n"
            f"Date:   {ts}\n\n"
            f"{msg}\n\n"
            f"{diff_text}"
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_sha(repo, short_sha: str) -> bytes | None:
        try:
            head = repo.refs[b"HEAD"]
        except KeyError:
            return None
        sha = head
        target = short_sha
        while sha:
            if sha.hex().startswith(target):
                return sha
            commit = repo[sha]
            if commit.type_name != b"commit":
                break
            sha = commit.parents[0] if commit.parents else None
        return None
