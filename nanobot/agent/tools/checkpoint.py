"""Checkpoint tools — save, browse, and restore work checkpoints.

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
# save_checkpoint — checkpoint the current state
# ===================================================================


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Directory to checkpoint. "
            "All file changes in this directory are recorded. "
            "If the directory has no internal storage yet, one is "
            "automatically set up (first save = first checkpoint).",
        ),
        "message": p(
            "string",
            "What happened, e.g. 'v2 - 初稿完成' or '改前备份'. "
            "Be descriptive so you can tell checkpoints apart later.",
        ),
    },
    required=["path"],
)
class SaveCheckpointTool(Tool):
    """Save the current state of your work as a checkpoint.

    Think of this as a save point — every file change in the
    directory gets recorded so you can review or undo it later.

    Uses git internally (no system git required).  Files listed in
    a ``.gitignore`` inside the directory are automatically skipped.

    Use ``list_checkpoints`` to browse history, ``restore_checkpoint`` to roll back.
    """

    name = "save_checkpoint"
    instruction = "Save a checkpoint before risky changes or after completing a milestone. Use restore_checkpoint to roll back."
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
            return "Nothing changed since the last save — checkpoint is already up to date."

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
# list_checkpoints — browse saved checkpoints
# ===================================================================


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Directory (or a file inside it) whose checkpoints to browse. "
            "The tool locates its internal storage and shows the checkpoint history. "
            "If no storage is found, use ``save_checkpoint`` first.",
        ),
        "sha": p(
            "string",
            "Which checkpoint to inspect in detail.  Shows the exact file changes "
            "in that checkpoint (what was added and how it changed). "
            "Omit this to see the checkpoint log.",
        ),
        "since": p(
            "string",
            "Only show checkpoints saved after this time, "
            "e.g. '7 days ago', '2024-01-01', '1 month ago'.",
        ),
        "max_stages": p(
            "integer",
            "How many recent checkpoints to show (default 20, max 50).",
            minimum=1,
            maximum=50,
            default=20,
        ),
    },
    required=["path"],
)
class ListCheckpointsTool(Tool):
    """Browse saved checkpoints and inspect what changed in each one.

    Two modes:
    - **Log mode** (default): list checkpoints with SHA, timestamp, and message.
      Filter by file path or time range.
    - **Diff mode** (pass ``sha``): show the exact file changes in a
      specific checkpoint.

    Works with any directory previously saved via ``save_checkpoint``.
    Pure Python, no system git required.
    """
    instruction = "List all available checkpoints with their SHA hashes. Use before restore_checkpoint to get the SHA."

    name = "list_checkpoints"
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
                "No checkpoints found.  "
                "Use ``save_checkpoint(path, message)`` to record the first one."
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
            return "No checkpoints saved yet."

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
            return "No matching checkpoints found."

        lines = [f"# Checkpoints — {Path(repo.path).name}"]
        lines.append("")
        if since:
            lines.append(f"Since: {since}")
        if rel_path:
            lines.append(f"File: {rel_path}")
        lines.append("")

        for e in entries:
            lines.append(f"  {e['sha']}  {e['timestamp']}  {e['message']}")

        lines.append("")
        lines.append(f"({len(entries)} checkpoints shown)")
        return "\n".join(lines)

    # -- diff view ---------------------------------------------------------

    def _diff(self, repo, sha: str) -> str:
        from dulwich.diff_tree import tree_changes
        from dulwich.patch import write_object_diff

        full_sha = _resolve_sha(repo, sha)
        if not full_sha:
            return f"Checkpoint '{sha}' not found.  Use ``list_checkpoints`` to list them."

        commit = repo[full_sha]
        if commit.type_name != b"commit":
            return f"'{sha}' is not a valid checkpoint."

        msg = commit.message.decode("utf-8", errors="replace").strip()
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit.commit_time))

        if not commit.parents:
            tree = repo[commit.tree]
            files = [entry.path.decode() for entry in tree.iteritems()]
            file_list = "\n".join(f"  A  {f}" for f in files) if files else "  (empty)"
            return (
                f"Checkpoint {sha[:8]}  —  First save\n"
                f"Date:   {ts}\n\n"
                f"{msg}\n\n"
                f"[First checkpoint — all files added]\n{file_list}"
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
            f"Checkpoint {sha[:8]}  —  Changes\n"
            f"Date:   {ts}\n\n"
            f"{msg}\n\n"
            f"{diff_text}"
        )


# ===================================================================
# restore_checkpoint — roll back to a previous checkpoint
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
            "Must have checkpoints (use ``save_checkpoint`` first).",
        ),
        "sha": p(
            "string",
            "Which checkpoint to go back to.  Get the SHA from ``list_checkpoints``.",
        ),
    },
    required=["path", "sha"],
)
class RestoreCheckpointTool(Tool):
    """Roll back files to a previously saved checkpoint.

    Reads files from the checkpoint and writes them to the working directory.
    Files that didn't exist in that checkpoint are left alone (safe rollback).
    """

    name = "restore_checkpoint"
    instruction = "Restore progress from a checkpoint. Use list_checkpoints first to get the SHA."
    read_only = False

    async def execute(self, path: str, sha: str, **kwargs: Any) -> str:
        from dulwich.repo import Repo

        repo_path = Path(path).resolve()
        git_root = _find_git_root(repo_path)
        if not git_root:
            return (
                "No checkpoints found in this directory.  "
                "Use ``save_checkpoint(path, message)`` first."
            )

        with Repo(str(git_root)) as repo:
            full_sha = _resolve_sha(repo, sha)
            if not full_sha:
                return f"Checkpoint '{sha}' not found.  Use ``list_checkpoints`` to list them."

            commit = repo[full_sha]
            if commit.type_name != b"commit":
                return f"'{sha}' is not a valid checkpoint."

            tree = repo[commit.tree]
            restored = _restore_tree(repo, tree, repo_path)

        if not restored:
            return f"Checkpoint {sha[:8]} has no files to restore."

        return (
            f"Restored {len(restored)} file(s) from checkpoint {sha[:8]}:\n"
            + "\n".join(f"  {f}" for f in restored)
        )