"""Stage (version control) tools — save, browse, and restore work stages.

Pure Python implementation using dulwich — tracks file changes with git
internally but needs no system ``git`` installed.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p


# ===================================================================
# Shared helpers
# ===================================================================


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.is_dir() or git_dir.is_file():
            return current
        current = current.parent
    return None


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
    from dulwich.diff_tree import tree_changes
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


# ===================================================================
# save_stage — save the current state as a new stage
# ===================================================================


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Directory to save as a new stage. "
            "All file changes in this directory are recorded. "
            "If the directory has no internal storage yet, one is "
            "automatically set up (first save = first stage).",
        ),
        "message": p(
            "string",
            "What happened in this stage, e.g. 'v2 - 初稿完成' or '添加了数据分析图表'. "
            "Be descriptive so you can tell stages apart later.",
        ),
    },
    required=["path", "message"],
)
class SaveStageTool(Tool):
    """Save the current state of your work as a named stage.

    Think of this as hitting "save point" — every file change in the
    directory gets recorded so you can review or undo it later.

    Uses git internally (no system git required).  Files listed in
    a ``.gitignore`` inside the directory are automatically skipped.

    Ask the user before saving: '当前阶段已完成，要保存一版吗？'
    Use ``show_stages`` to browse history, ``restore_stage`` to roll back.
    """

    name = "save_stage"
    read_only = False

    async def execute(self, path: str, message: str = "", **kwargs: Any) -> str:
        from dulwich import porcelain

        repo_path = Path(path).resolve()
        repo_path.mkdir(parents=True, exist_ok=True)
        if not (repo_path / ".git").is_dir():
            porcelain.init(str(repo_path))
            logger.info("save_stage: git init at {}", repo_path)

        st = porcelain.status(str(repo_path))
        unstaged = [f.decode() for f in st.unstaged]
        untracked = [f.decode() for f in st.untracked]
        has_staged = any(st.staged.values())

        if not unstaged and not untracked and not has_staged:
            return "Nothing changed since the last save — stage is already up to date."

        to_stage = unstaged + untracked
        if to_stage:
            porcelain.add(str(repo_path), paths=to_stage)

        lines: list[str] = []
        for f in unstaged:
            lines.append(f"  modified: {f}")
        for f in untracked:
            lines.append(f"  new: {f}")

        msg_bytes = (message or "save_stage").encode("utf-8")
        sha_bytes = porcelain.commit(
            str(repo_path),
            message=msg_bytes,
            author=b"nanobot <nanobot@nanobot>",
            committer=b"nanobot <nanobot@nanobot>",
        )
        sha = sha_bytes.hex()[:8] if sha_bytes else "unknown"
        out = f"Saved {sha}: {message}\n" + "\n".join(lines) if lines else f"Saved {sha}: {message}"
        return out


# ===================================================================
# show_stages — browse saved stages and inspect changes
# ===================================================================


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Directory (or a file inside it) whose saved stages to browse. "
            "The tool locates its internal storage and shows the stage history. "
            "If no storage is found, use ``save_stage`` first.",
        ),
        "sha": p(
            "string",
            "Which stage to inspect in detail.  Shows the exact file changes "
            "in that stage (what was added and how it changed). "
            "Omit this to see the stage log.",
        ),
        "since": p(
            "string",
            "Only show stages saved after this time, "
            "e.g. '7 days ago', '2024-01-01', '1 month ago'.",
        ),
        "max_stages": p(
            "integer",
            "How many recent stages to show (default 20, max 50).",
            minimum=1,
            maximum=50,
            default=20,
        ),
    },
    required=["path"],
)
class ShowStagesTool(Tool):
    """Browse saved stages and inspect what changed in each one.

    Two modes:
    - **Log mode** (default): list stages with SHA, timestamp, and message.
      Filter by file path or time range.
    - **Diff mode** (pass ``sha``): show the exact file changes in a
      specific stage.

    Works with any directory previously saved via ``save_stage``.
    Pure Python, no system git required.
    """

    name = "show_stages"
    read_only = True

    async def execute(
        self,
        path: str,
        sha: str | None = None,
        since: str | None = None,
        max_stages: int = 20,
        **kwargs: Any,
    ) -> str:
        from dulwich.repo import Repo

        path = path.replace("\\", "/")
        abs_path = Path(path).resolve()
        git_root = _find_git_root(abs_path)
        if not git_root:
            return (
                "No stages found.  "
                "Use ``save_stage(path, message)`` to record the first one."
            )

        try:
            rel_path: str | None = None
            if abs_path != git_root:
                rel_path = abs_path.relative_to(git_root).as_posix()
        except ValueError:
            rel_path = None

        with Repo(str(git_root)) as repo:
            if sha:
                return self._diff(repo, sha)
            return self._log(repo, since, rel_path, max_stages)

    # -- log view ----------------------------------------------------------

    def _log(
        self, repo, since: str | None, rel_path: str | None,
        max_stages: int,
    ) -> str:
        since_ts = _parse_since(since) if since else None

        try:
            head = repo.refs[b"HEAD"]
        except KeyError:
            return "No stages saved yet."

        entries: list[dict[str, Any]] = []
        sha: bytes | None = head
        while sha and len(entries) < max_stages:
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
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit.commit_time))
            entries.append({
                "sha": sha.hex()[:8],
                "timestamp": ts,
                "message": msg,
            })
            sha = commit.parents[0] if commit.parents else None

        if not entries:
            return "No matching stages found."

        lines = [f"# Stages — {Path(repo.path).name}"]
        lines.append("")
        if since:
            lines.append(f"Since: {since}")
        if rel_path:
            lines.append(f"File: {rel_path}")
        lines.append("")

        for e in entries:
            lines.append(f"  {e['sha']}  {e['timestamp']}  {e['message']}")

        lines.append("")
        lines.append(f"({len(entries)} stages shown)")
        return "\n".join(lines)

    # -- diff view ---------------------------------------------------------

    def _diff(self, repo, sha: str) -> str:
        from dulwich.diff_tree import tree_changes
        from dulwich.patch import write_object_diff

        full_sha = _resolve_sha(repo, sha)
        if not full_sha:
            return f"Stage '{sha}' not found.  Use ``show_stages`` to list them."

        commit = repo[full_sha]
        if commit.type_name != b"commit":
            return f"'{sha}' is not a valid stage."

        msg = commit.message.decode("utf-8", errors="replace").strip()
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit.commit_time))

        if not commit.parents:
            tree = repo[commit.tree]
            files = [entry.path.decode() for entry in tree.iteritems()]
            file_list = "\n".join(f"  A  {f}" for f in files) if files else "  (empty)"
            return (
                f"Stage {sha[:8]}  —  First save\n"
                f"Date:   {ts}\n\n"
                f"{msg}\n\n"
                f"[First stage — all files added]\n{file_list}"
            )

        buf = BytesIO()
        parent = repo[commit.parents[0]]
        try:
            for change in tree_changes(repo.object_store, parent.tree, commit.tree):
                write_object_diff(buf, repo.object_store, change.old, change.new)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"

        diff_text = buf.getvalue().decode("utf-8", errors="replace")
        if not diff_text.strip():
            diff_text = "  (no file changes)"

        return (
            f"Stage {sha[:8]}  —  Changes\n"
            f"Date:   {ts}\n\n"
            f"{msg}\n\n"
            f"{diff_text}"
        )


# ===================================================================
# restore_stage — roll back to a previous stage
# ===================================================================


def _restore_tree(repo, tree_obj, repo_path: Path, prefix: str = "") -> list[str]:
    restored: list[str] = []
    for name, _mode, sha in tree_obj.iteritems():
        name_str = name.decode()
        rel_path = f"{prefix}/{name_str}" if prefix else name_str
        obj = repo[sha]
        if obj.type_name == b"tree":
            restored.extend(_restore_tree(repo, obj, repo_path, rel_path))
        else:
            target = repo_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(obj.data)
            restored.append(rel_path)
    return restored


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Directory whose files should be rolled back. "
            "Must have stages saved (use ``save_stage`` first).",
        ),
        "sha": p(
            "string",
            "Which stage to go back to.  Get the SHA from ``show_stages``.",
        ),
    },
    required=["path", "sha"],
)
class RestoreStageTool(Tool):
    """Roll back files to a previously saved stage.

    Reads files from the saved stage and writes them to the working directory.
    Files that didn't exist in that stage are left alone (safe rollback).

    Before rolling back, ask: '要不要先保存当前阶段？'
    That way no work is lost even after a rollback.
    """

    name = "restore_stage"
    read_only = False

    async def execute(self, path: str, sha: str, **kwargs: Any) -> str:
        from dulwich.repo import Repo

        repo_path = Path(path).resolve()
        git_root = _find_git_root(repo_path)
        if not git_root:
            return (
                "No stages found in this directory.  "
                "Use ``save_stage(path, message)`` first."
            )

        with Repo(str(git_root)) as repo:
            full_sha = _resolve_sha(repo, sha)
            if not full_sha:
                return f"Stage '{sha}' not found.  Use ``show_stages`` to list them."

            commit = repo[full_sha]
            if commit.type_name != b"commit":
                return f"'{sha}' is not a valid stage."

            tree = repo[commit.tree]
            restored = _restore_tree(repo, tree, repo_path)

        if not restored:
            return f"Stage {sha[:8]} has no files to restore."

        return (
            f"Restored {len(restored)} file(s) from stage {sha[:8]}:\n"
            + "\n".join(f"  {f}" for f in restored)
        )
