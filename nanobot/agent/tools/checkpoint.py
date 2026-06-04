"""Checkpoint tools — git versioning for LLM task outputs.

Two tools sharing dulwich-based git operations:

- ``checkpoint(path, message)`` — save a version snapshot
- ``restore(path, sha)`` — restore files from a previous version

Use ``git_inspect`` to view version history and inspect changes.
No system ``git`` required — all operations use the pure-Python dulwich library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ensure_repo(path: str) -> Path:
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    if not (p / ".git").is_dir():
        from dulwich import porcelain

        porcelain.init(str(p))
        logger.info("checkpoint: git init at {}", p)
    return p


# ---------------------------------------------------------------------------
# Tool: checkpoint — save a version snapshot
# ---------------------------------------------------------------------------


@tool_parameters(
    properties={
        "path": p(
            "string",
            "Absolute path to the directory to version. "
            "All file changes in this directory will be saved as a new version. "
            "If the directory doesn't exist it will be created. "
            "If it doesn't have a git repo yet, one is automatically initialized.",
        ),
        "message": p(
            "string",
            "Human-readable description of this version, "
            "e.g. 'v2 - 配色调整' or '初版完成'.",
        ),
    },
    required=["path", "message"],
)
class CheckpointTool(Tool):
    """Save a version snapshot of all files in a directory (git-based versioning).

    Creates a git commit using dulwich (no system git required).
    If the directory doesn't have a git repo yet, one is automatically created.
    Files already listed in ``.gitignore`` are excluded.

    Use at natural milestones — ask the user first:
    '当前版本已完成，要保存一版吗？'

    To exclude files, write patterns to ``.gitignore`` *before* calling.
    Use ``git_inspect`` to view history and ``restore`` to revert.
    """

    name = "checkpoint"
    read_only = False

    async def execute(self, path: str, message: str = "", **kwargs: Any) -> str:
        from dulwich import porcelain

        repo_path = _ensure_repo(path)

        st = porcelain.status(str(repo_path))
        unstaged = [f.decode() for f in st.unstaged]
        untracked = [f.decode() for f in st.untracked]
        has_staged = any(st.staged.values())

        if not unstaged and not untracked and not has_staged:
            return "No changes to save — everything is already up to date."

        to_stage = unstaged + untracked
        if to_stage:
            porcelain.add(str(repo_path), paths=to_stage)

        lines: list[str] = []
        for f in unstaged:
            lines.append(f"  modified: {f}")
        for f in untracked:
            lines.append(f"  new: {f}")

        msg_bytes = (message or "checkpoint").encode("utf-8")
        sha_bytes = porcelain.commit(
            str(repo_path),
            message=msg_bytes,
            author=b"nanobot <nanobot@nanobot>",
            committer=b"nanobot <nanobot@nanobot>",
        )
        sha = sha_bytes.hex()[:8] if sha_bytes else "unknown"
        out = f"Saved {sha}: {message}\n" + "\n".join(lines) if lines else f"Saved {sha}: {message}"
        return out


# ---------------------------------------------------------------------------
# Tool: restore — restore files from a previous version
# ---------------------------------------------------------------------------


def _restore_tree(repo, tree_obj, repo_path: Path, prefix: str = "") -> list[str]:
    restored: list[str] = []
    for name, mode, sha in tree_obj.iteritems():
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
            "Absolute path to the directory to restore files into. "
            "Must be a directory previously versioned with ``checkpoint``.",
        ),
        "sha": p(
            "string",
            "The commit SHA (or prefix) to restore. "
            "Use ``git_inspect`` first to find the target SHA.",
        ),
    },
    required=["path", "sha"],
)
class RestoreTool(Tool):
    """Restore files to a previously saved version (git-based rollback).

    Reads files from the saved commit and writes them to the working directory.
    Files that don't exist in the target version are NOT deleted.

    Before restoring, ask the user if they want to save the current version
    first, so their recent work isn't lost.
    """

    name = "restore"
    read_only = False

    async def execute(self, path: str, sha: str, **kwargs: Any) -> str:
        from dulwich.repo import Repo

        repo_path = Path(path).resolve()
        git_dir = repo_path / ".git"
        if not git_dir.is_dir() and not git_dir.is_file():
            return (
                "Error: No git repository in this directory. "
                "Use ``checkpoint(path, message)`` first to initialize version control."
            )

        with Repo(str(repo_path)) as repo:
            full_sha = self._resolve_sha(repo, sha)
            if not full_sha:
                return (
                    f"Error: Version '{sha}' not found. "
                    "Use ``git_inspect`` to see available versions."
                )

            commit = repo[full_sha]
            if commit.type_name != b"commit":
                return f"Error: '{sha}' is not a valid commit."

            tree = repo[commit.tree]
            restored = _restore_tree(repo, tree, repo_path)

        if not restored:
            return f"Version {sha[:8]} contains no files to restore."

        return (
            f"Restored {len(restored)} file(s) from {sha[:8]}:\n"
            + "\n".join(f"  {f}" for f in restored)
        )

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
