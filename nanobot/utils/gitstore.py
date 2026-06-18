"""Git-backed version control for memory files, using dulwich."""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


@dataclass
class CommitInfo:
    sha: str  # Short SHA (8 chars)
    message: str
    timestamp: str  # Formatted datetime

    def format(self, diff: str = "") -> str:
        """Format this commit for display, optionally with a diff."""
        header = f"## {self.message.splitlines()[0]}\n`{self.sha}` — {self.timestamp}\n"
        if diff:
            return f"{header}\n```diff\n{diff}\n```"
        return f"{header}\n(no file changes)"


@dataclass
class LineAge:
    """Age of a single line based on git blame."""

    age_days: int  # days since last modification


def _compute_line_ages(annotated) -> list[LineAge]:
    """Convert annotate results to per-line ages."""
    now = datetime.now(tz=timezone.utc).date()
    ages: list[LineAge] = []
    for (commit, _tree_entry), _line_bytes in annotated:
        dt = datetime.fromtimestamp(commit.commit_time, tz=timezone.utc).date()
        ages.append(LineAge(age_days=(now - dt).days))
    return ages


class GitStore:
    """Git-backed version control for memory files."""

    def __init__(self, workspace: Path, tracked_files: list[str]):
        self._workspace = workspace
        self._tracked_files = tracked_files

    def is_initialized(self) -> bool:
        """Check if the git repo has been initialized."""
        return (self._workspace / ".git").is_dir()

    # -- init ------------------------------------------------------------------

    def init(self) -> bool:
        """Initialize a git repo if not already initialized.

        Creates .gitignore and makes an initial commit.
        Returns True if a new repo was created, False if already exists.
        """
        if self.is_initialized():
            return False

        if self._is_inside_git_repo():
            logger.warning(
                "Workspace {} is already inside a git repo; "
                "skipping nested repo initialization",
                self._workspace,
            )
            return False

        try:
            from dulwich import porcelain

            porcelain.init(str(self._workspace))

            # Write .gitignore (merge with existing if present)
            gitignore = self._workspace / ".gitignore"
            gitignore_content = self._build_gitignore()
            if gitignore.exists():
                existing = gitignore.read_text(encoding="utf-8")
                existing_lines = set(existing.splitlines())
                new_lines = [
                    line
                    for line in gitignore_content.splitlines()
                    if line not in existing_lines
                ]
                if new_lines:
                    merged = existing.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n"
                    gitignore.write_text(merged, encoding="utf-8")
            else:
                gitignore.write_text(gitignore_content, encoding="utf-8")

            # Ensure tracked files exist (touch them if missing) so the initial
            # commit has something to track.
            for rel in self._tracked_files:
                p = self._workspace / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_text("", encoding="utf-8")

            # Discover existing memory files for the initial commit.
            initial_memory = sorted(
                str(p.relative_to(self._workspace))
                for p in self._workspace.glob("memory/**/*.md")
            )

            # Initial commit
            porcelain.add(str(self._workspace), paths=[".gitignore"] + self._tracked_files + initial_memory)
            porcelain.commit(
                str(self._workspace),
                message=b"init: nanobot memory store",
                author=b"nanobot <nanobot@nanobot>",
                committer=b"nanobot <nanobot@nanobot>",
            )
            logger.info("Git store initialized at {}", self._workspace)
            return True
        except Exception:
            logger.warning("Git store init failed for {}", self._workspace, exc_info=True)
            return False

    # -- daily operations ------------------------------------------------------

    def auto_commit(self, message: str) -> str | None:
        """Stage tracked memory files and commit if there are changes.

        Returns the short commit SHA, or None if nothing to commit.
        """
        if not self.is_initialized():
            return None

        try:
            from dulwich import porcelain

            # .gitignore allows memory/ dir; dynamically discover memory files.
            st = porcelain.status(str(self._workspace))
            if not st.unstaged and not any(st.staged.values()):
                return None

            memory_files = sorted(
                str(p.relative_to(self._workspace))
                for p in self._workspace.glob("memory/**/*.md")
            )
            paths = self._tracked_files + memory_files

            msg_bytes = message.encode("utf-8") if isinstance(message, str) else message
            porcelain.add(str(self._workspace), paths=paths)
            sha_bytes = porcelain.commit(
                str(self._workspace),
                message=msg_bytes,
                author=b"nanobot <nanobot@nanobot>",
                committer=b"nanobot <nanobot@nanobot>",
            )
            if sha_bytes is None:
                return None
            sha = sha_bytes.hex()[:8]
            logger.debug("Git auto-commit: {} ({})", sha, message)
            return sha
        except Exception:
            logger.warning("Git auto-commit failed: {}", message, exc_info=True)
            return None

    # -- internal helpers ------------------------------------------------------

    def _resolve_sha(self, short_sha: str) -> bytes | None:
        """Resolve a short SHA prefix to the full SHA bytes."""
        try:
            from dulwich.repo import Repo

            with Repo(str(self._workspace)) as repo:
                try:
                    sha = repo.refs[b"HEAD"]
                except KeyError:
                    return None

                while sha:
                    if sha.hex().startswith(short_sha):
                        return sha
                    commit = repo[sha]
                    if commit.type_name != b"commit":
                        break
                    sha = commit.parents[0] if commit.parents else None
            return None
        except Exception:
            return None

    def _is_inside_git_repo(self) -> bool:
        """Check if self._workspace is already inside a git repository.

        Walks up from self._workspace to the filesystem root, returning True
        if any parent directory contains a .git entry.

        Git worktrees and submodules can use a ``.git`` file instead of a
        directory, so we must treat either form as "already inside a repo".
        """
        current = self._workspace.resolve()
        while current != current.parent:
            if (current / ".git").exists():
                return True
            current = current.parent
        return False

    def _build_gitignore(self) -> str:
        """Generate .gitignore — allow tracked root files + memory/ dir."""
        allowed = sorted(set(self._tracked_files))
        lines = ["/*"]
        for f in allowed:
            lines.append(f"!{f}")
        lines.append("!memory/")
        lines.append("memory/.vector_index/")
        lines.append("!.gitignore")
        return "\n".join(lines) + "\n"

    # -- query -----------------------------------------------------------------

    def log(self, max_entries: int = 20) -> list[CommitInfo]:
        """Return simplified commit log."""
        if not self.is_initialized():
            return []

        try:
            from dulwich.repo import Repo

            entries: list[CommitInfo] = []
            with Repo(str(self._workspace)) as repo:
                try:
                    head = repo.refs[b"HEAD"]
                except KeyError:
                    return []

                sha = head
                while sha and len(entries) < max_entries:
                    commit = repo[sha]
                    if commit.type_name != b"commit":
                        break
                    ts = time.strftime(
                        "%Y-%m-%d %H:%M",
                        time.localtime(commit.commit_time),
                    )
                    msg = commit.message.decode("utf-8", errors="replace").strip()
                    entries.append(CommitInfo(
                        sha=sha.hex()[:8],
                        message=msg,
                        timestamp=ts,
                    ))
                    sha = commit.parents[0] if commit.parents else None

            return entries
        except Exception:
            logger.warning("Git log failed", exc_info=True)
            return []

    def line_ages(self, file_path: str) -> list[LineAge]:
        """Compute the age of each line in a tracked file via git blame.

        Returns one LineAge per line, in order.
        Returns an empty list if the repo is not initialized, the file is
        empty, or annotation fails.
        """

        if not self.is_initialized():
            return []

        target = self._workspace / file_path
        if not target.exists() or target.stat().st_size == 0:
            return []

        try:
            from dulwich import porcelain

            annotated = porcelain.annotate(str(self._workspace), file_path)
        except Exception:
            logger.warning("Git line_ages annotate failed for {}", file_path, exc_info=True)
            return []

        if not annotated:
            return []

        return _compute_line_ages(annotated)

    def diff_commits(self, sha1: str, sha2: str) -> str:
        """Show diff between two commits."""
        if not self.is_initialized():
            return ""

        try:
            from dulwich import porcelain

            full1 = self._resolve_sha(sha1)
            full2 = self._resolve_sha(sha2)
            if not full1 or not full2:
                return ""

            out = io.BytesIO()
            porcelain.diff(
                str(self._workspace),
                commit=full1,
                commit2=full2,
                outstream=out,
            )
            return out.getvalue().decode("utf-8", errors="replace")
        except Exception:
            logger.warning("Git diff_commits failed", exc_info=True)
            return ""

    def find_commit(self, short_sha: str, max_entries: int = 20) -> CommitInfo | None:
        """Find a commit by short SHA prefix match."""
        for c in self.log(max_entries=max_entries):
            if c.sha.startswith(short_sha):
                return c
        return None

    def show_commit_diff(self, short_sha: str, max_entries: int = 20) -> tuple[CommitInfo, str] | None:
        """Find a commit and return it with its diff vs the parent."""
        commits = self.log(max_entries=max_entries)
        for i, c in enumerate(commits):
            if c.sha.startswith(short_sha):
                if i + 1 < len(commits):
                    diff = self.diff_commits(commits[i + 1].sha, c.sha)
                else:
                    diff = ""
                return c, diff
        return None

    # -- restore ---------------------------------------------------------------

    def revert(self, commit: str) -> str | None:
        """Revert (undo) the changes introduced by the given commit.

        Restores all tracked memory files to the state at the commit's parent,
        then creates a new commit recording the revert.

        Returns the new commit SHA, or None on failure.
        """
        if not self.is_initialized():
            return None

        try:
            from dulwich.repo import Repo

            full_sha = self._resolve_sha(commit)
            if not full_sha:
                logger.warning("Git revert: SHA not found: {}", commit)
                return None

            with Repo(str(self._workspace)) as repo:
                commit_obj = repo[full_sha]
                if commit_obj.type_name != b"commit":
                    return None

                if not commit_obj.parents:
                    logger.warning("Git revert: cannot revert root commit {}", commit)
                    return None

                # Use the parent's tree — this undoes the commit's changes
                parent_obj = repo[commit_obj.parents[0]]
                tree = repo[parent_obj.tree]

                restored: list[str] = []
                for filepath in self._tracked_files:
                    content = self._read_blob_from_tree(repo, tree, filepath)
                    if content is not None:
                        dest = self._workspace / filepath
                        dest.write_text(content, encoding="utf-8")
                        restored.append(filepath)

            if not restored:
                return None

            # Commit the restored state
            msg = f"revert: undo {commit}"
            return self.auto_commit(msg)
        except Exception:
            logger.warning("Git revert failed for {}", commit, exc_info=True)
            return None

    @staticmethod
    def _read_blob_from_tree(repo, tree, filepath: str) -> str | None:
        """Read a blob's content from a tree object by walking path parts."""
        parts = Path(filepath).parts
        current = tree
        for part in parts:
            try:
                entry = current[part.encode()]
            except KeyError:
                return None
            obj = repo[entry[1]]
            if obj.type_name == b"blob":
                return obj.data.decode("utf-8", errors="replace")
            if obj.type_name == b"tree":
                current = obj
            else:
                return None
        return None


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Only creates missing files."""
    from importlib.resources import files as pkg_files

    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []

    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("."):
            _write(item, workspace / item.name)
    for item in (tpl / "memory").rglob("*.md"):
        _write(item, workspace / "memory" / item.relative_to(tpl / "memory"))
    for item in (tpl / "framework").rglob("*.md"):
        _write(item, workspace / "framework" / item.relative_to(tpl / "framework"))
    (workspace / "skills").mkdir(exist_ok=True)

    # Create tasks/ directory with tree.json, tree.schema.md, CURRENT.md, and team_board.md
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    tree_content = json.dumps({"schema_version": 1, "items": []}, indent=2, ensure_ascii=False)
    current_content = "# Current State — tasks/CURRENT.md\n"
    team_board_content = "# Team Board — tasks/team_board.md\n\nShare findings, blockers, and insights here so the whole team benefits.\n"
    schema_content = (
        "# tree.json Schema Reference\n\n"
        "tree.json is the task tree data file at `tasks/tree.json`.\n\n"
        "## Structure\n\n"
        "```json\n"
        '{\n  "schema_version": 1,\n  "items": [\n'
        '    {\n      "id": "unique-node-id",\n'
        '      "name": "Node display name",\n'
        '      "status": "active | completed | failed | paused | pending",\n'
        '      "criteria": "What counts as done (verification standard)",\n'
        '      "parent": null | "parent-node-id",\n'
        '      "doc": null | "relative/path/to/node-doc.md",\n'
        '      "note": null | "Why failed/paused, key decisions",\n'
        '      "created": "YYYY-MM-DD",\n'
        '      "updated": "YYYY-MM-DD",\n'
        '      "completed": null | "YYYY-MM-DD"\n'
        "    }\n  ]\n}\n"
        "```\n\n"
        "## Fields\n\n"
        "| Field | Required | Description |\n"
        "|-------|----------|-------------|\n"
        "| `id` | yes | Unique identifier, kebab-case |\n"
        "| `name` | yes | Display name |\n"
        "| `status` | yes | pending / active / completed / failed / paused |\n"
        "| `criteria` | yes | Success criteria for verification |\n"
        "| `parent` | yes | Parent node id, null for root |\n"
        "| `doc` | no | Path to node document |\n"
        "| `note` | no | Failure reasons, key decisions |\n"
        "| `created` | yes | Creation date YYYY-MM-DD |\n"
        "| `updated` | yes | Last update date YYYY-MM-DD |\n"
        "| `completed` | no | Completion date YYYY-MM-DD |\n\n"
        "## Operations\n\n"
        "- Read: `read_file_tool` reads `tasks/tree.json`\n"
        "- Write: `edit_file_tool` / `write_file_tool` modifies JSON\n"
        "- Do not delete historical nodes. failed/paused preserve trace\n"
        "- Archive: root completed --> children moved to `tasks/<project-id>/index.md` and removed from tree.json\n"
    )
    for name, content in (
        ("tree.json", tree_content),
        ("tree.schema.md", schema_content),
        ("CURRENT.md", current_content),
        ("team_board.md", team_board_content),
    ):
        f = tasks_dir / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
            added.append(f"tasks/{name}")

    # Initialize self-installed tools directory and regenerate index
    from nanobot.utils.tools_index import init_tools_dir, rebuild_tools_index

    init_tools_dir(workspace)
    rebuild_tools_index(workspace)

    if added and not silent:
        from rich.console import Console

        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")

    # Initialize git for memory version control
    try:
        from nanobot.utils.gitstore import GitStore

        gs = GitStore(
            workspace,
            tracked_files=[
                "SOUL.md",
                "USER.md",
            ],
        )
        gs.init()
    except Exception:
        logger.warning("Failed to initialize git store for {}", workspace)

    return added
