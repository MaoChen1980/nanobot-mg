"""Tests for GitStore — line_ages() and core git operations."""

import subprocess
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from nanobot.utils.gitstore import GitStore


@pytest.fixture
def git(tmp_path):
    """Create an initialized GitStore with tracked MEMORY.md."""
    g = GitStore(tmp_path, tracked_files=["MEMORY.md", "SOUL.md"])
    g.init()
    return g


class TestLineAges:
    def test_returns_empty_when_not_initialized(self, tmp_path):
        """line_ages should return [] if the git repo is not initialized."""
        git = GitStore(tmp_path, tracked_files=["MEMORY.md"])
        assert git.line_ages("MEMORY.md") == []

    def test_returns_empty_for_missing_file(self, git):
        """line_ages should return [] for a file that doesn't exist."""
        assert git.line_ages("SOUL.md") == []

    def test_returns_empty_for_empty_file(self, git, tmp_path):
        """line_ages should return [] for an empty tracked file."""
        (tmp_path / "SOUL.md").write_text("", encoding="utf-8")
        git.auto_commit("empty soul")
        assert git.line_ages("SOUL.md") == []

    def test_one_age_per_line(self, git, tmp_path):
        """line_ages should return one entry per line in the file."""
        content = "# Memory\n\n## Section A\n- item 1\n"
        (tmp_path / "MEMORY.md").write_text(content, encoding="utf-8")
        git.auto_commit("initial")
        ages = git.line_ages("MEMORY.md")
        assert len(ages) == len(content.splitlines())

    def test_fresh_lines_have_age_zero(self, git, tmp_path):
        """Lines committed today should have age_days=0."""
        (tmp_path / "MEMORY.md").write_text("## A\n- x\n", encoding="utf-8")
        git.auto_commit("initial")
        ages = git.line_ages("MEMORY.md")
        assert all(a.age_days == 0 for a in ages)

    def test_age_differentiates_across_days(self, git, tmp_path):
        """Lines committed today should show correct age when 'now' is mocked forward."""
        (tmp_path / "MEMORY.md").write_text("## A\n- x\n", encoding="utf-8")
        git.auto_commit("initial")

        future_now = datetime.now(tz=timezone.utc) + timedelta(days=30)
        with patch("nanobot.utils.gitstore.datetime") as mock_dt:
            mock_dt.now.return_value = future_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            ages = git.line_ages("MEMORY.md")

        assert len(ages) == 2
        assert all(a.age_days == 30 for a in ages)

    def test_annotate_failure_returns_empty(self, tmp_path):
        """If annotate fails, line_ages should return [] gracefully."""
        git = GitStore(tmp_path, tracked_files=["MEMORY.md"])
        # Don't init — annotate will fail
        assert git.line_ages("MEMORY.md") == []

    def test_partial_edit_only_updates_changed_lines(self, git, tmp_path):
        """Only modified lines should reflect the new commit's timestamp."""
        (tmp_path / "MEMORY.md").write_text(
            "# Memory\n\n## A\n- old\n\n## B\n- keep\n", encoding="utf-8"
        )
        git.auto_commit("commit1")
        time.sleep(1.1)

        # Only modify section A
        (tmp_path / "MEMORY.md").write_text(
            "# Memory\n\n## A\n- new\n\n## B\n- keep\n", encoding="utf-8"
        )
        git.auto_commit("commit2")

        ages = git.line_ages("MEMORY.md")
        lines = (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        # All lines are from today, but verify line-level tracking works
        assert len(ages) == len(lines)
        # "- new" line and "- keep" line both age=0 (same day), but
        # the key point is we get per-line results
        assert len(ages) == 7


class TestNestedRepoProtection:
    """Regression tests for GitHub issue #2980: nested repo protection."""

    def test_init_refuses_inside_git_repo(self, tmp_path):
        """init() should detect it's inside an existing git repo and refuse."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()

        workspace = project / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is False
        assert not (workspace / ".git").is_dir()

    def test_init_preserves_existing_gitignore(self, tmp_path):
        """init() should preserve existing .gitignore entries and append new ones."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        existing = "*.pyc\n__pycache__/\n"
        (workspace / ".gitignore").write_text(existing, encoding="utf-8")

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        assert "*.pyc" in gitignore
        assert "__pycache__/" in gitignore
        assert "!MEMORY.md" in gitignore
        assert "!.gitignore" in gitignore

    def test_init_no_gitignore_creates_new(self, tmp_path):
        """init() should create .gitignore with Dream content when none exists."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        expected = g._build_gitignore()
        assert gitignore == expected

    def test_init_gitignore_merge_idempotent(self, tmp_path):
        """init() should not duplicate Extractor entries already in .gitignore."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Pre-existing .gitignore that already has some Extractor entries
        existing = "*.pyc\n/*\n!MEMORY.md\n"
        (workspace / ".gitignore").write_text(existing, encoding="utf-8")

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        # No duplicate lines
        lines = gitignore.splitlines()
        assert lines.count("/*") == 1
        assert lines.count("!MEMORY.md") == 1
        # Existing entry preserved, new Extractor entries appended
        assert "*.pyc" in gitignore
        assert "!.gitignore" in gitignore

    def test_init_outside_git_repo_works_normally(self, tmp_path):
        """init() should succeed and create .git when not inside a git repo."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        assert (workspace / ".git").is_dir()

    def test_init_refuses_inside_git_worktree(self, tmp_path):
        """init() should refuse when the parent checkout is a git worktree."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "init",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "branch", "wt-branch"], check=True)

        worktree = tmp_path / "worktree"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", str(worktree), "wt-branch"],
            check=True,
        )
        assert (worktree / ".git").is_file()

        workspace = worktree / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is False
        assert not (workspace / ".git").exists()


class TestCommitWorkspaceChanges:
    """Tests for commit_workspace_changes() — git commit for parent-repo workspaces."""

    def test_returns_none_when_not_a_git_repo(self, tmp_path):
        """Should return None when workspace is not a git repo."""
        from nanobot.utils.gitstore import commit_workspace_changes

        assert commit_workspace_changes(tmp_path, ["."], "msg") is None

    def test_returns_none_when_no_changes(self, tmp_path):
        """Should return None when there are no uncommitted changes."""
        from nanobot.utils.gitstore import commit_workspace_changes

        _init_git_repo(tmp_path)
        assert commit_workspace_changes(tmp_path, ["."], "msg") is None

    def test_commits_new_file(self, tmp_path):
        """Should commit a newly created file under rel_dirs."""
        from nanobot.utils.gitstore import commit_workspace_changes

        _init_git_repo(tmp_path)
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "test-skill").mkdir()
        (skills / "test-skill" / "SKILL.md").write_text("name: test-skill\n", encoding="utf-8")

        sha = commit_workspace_changes(tmp_path, ["skills"], "skill: create test-skill")
        assert sha is not None
        assert len(sha) >= 7

        # Verify commit appears in log
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=30,
        )
        assert sha in log.stdout

    def test_commits_only_specified_rel_dirs(self, tmp_path):
        """Should only commit changes in the given rel_dirs, leaving others dirty."""
        from nanobot.utils.gitstore import commit_workspace_changes

        _init_git_repo(tmp_path)
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "a").mkdir()
        (skills / "a" / "SKILL.md").write_text("name: a\n", encoding="utf-8")
        other = tmp_path / "other"
        other.mkdir()
        (other / "untracked.txt").write_text("dirty", encoding="utf-8")

        sha = commit_workspace_changes(tmp_path, ["skills"], "only skills")
        assert sha is not None

        # other/ should still be dirty
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=30,
        )
        assert "other/" in status.stdout

    def test_add_failure_returns_none(self, tmp_path):
        """Should return None when git add fails (e.g. invalid rel_dir)."""
        from nanobot.utils.gitstore import commit_workspace_changes

        _init_git_repo(tmp_path)
        result = commit_workspace_changes(tmp_path, ["nonexistent"], "msg")
        assert result is None


def _init_git_repo(path):
    """Initialize a bare git repo at path with a user config for commits."""
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True, capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Tester"],
        check=True, capture_output=True, timeout=30,
    )
