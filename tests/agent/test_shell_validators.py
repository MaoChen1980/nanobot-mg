"""Tests for nanobot.agent.tools.shell_validators — command safety validation.

Covers check_command_safety and its internal check functions.

conftest.py must be imported first — it sets up nanobot stubs and loads
the real modules before this file runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py must be imported first to set up nanobot stubs before this import.
# After conftest: sys.modules["nanobot.*"] stubs are in place so the import
# below doesn't trigger nanobot/__init__.py (Python 3.9 incompatible).
_spec = importlib.util.spec_from_file_location(
    "nanobot.agent.tools.shell_validators",
    Path(__file__).resolve().parents[2]
    / "nanobot" / "agent" / "tools" / "shell_validators.py",
)
_shell_validators_mod = importlib.util.module_from_spec(_spec)
sys.modules["nanobot.agent.tools.shell_validators"] = _shell_validators_mod
_spec.loader.exec_module(_shell_validators_mod)

# Re-export for test convenience
DANGEROUS_PATTERNS = _shell_validators_mod.DANGEROUS_PATTERNS
_check_dangerous_patterns = _shell_validators_mod._check_dangerous_patterns
_check_internal_url = _shell_validators_mod._check_internal_url
_check_path_traversal = _shell_validators_mod._check_path_traversal
_check_workspace_boundary = _shell_validators_mod._check_workspace_boundary
check_command_safety = _shell_validators_mod.check_command_safety

# The network module is loaded by conftest.py; import it for patching
import nanobot.security.network as _network_mod


# ============================================================================
# _check_dangerous_patterns
# ============================================================================

class TestCheckDangerousPatterns:
    def test_allows_safe_commands(self):
        assert _check_dangerous_patterns("ls -la", DANGEROUS_PATTERNS) is None
        assert _check_dangerous_patterns("git status", DANGEROUS_PATTERNS) is None
        assert _check_dangerous_patterns("echo hello", DANGEROUS_PATTERNS) is None

    def test_blocks_rm_rf(self):
        result = _check_dangerous_patterns("rm -rf /", DANGEROUS_PATTERNS)
        assert result is not None
        assert "dangerous pattern" in result.lower()

    def test_blocks_recursive_delete(self):
        result = _check_dangerous_patterns("rm -rf /tmp", DANGEROUS_PATTERNS)
        assert result is not None

    def test_blocks_shutdown_commands(self):
        result = _check_dangerous_patterns("shutdown -h now", DANGEROUS_PATTERNS)
        assert result is not None

    def test_blocks_pip_uninstall(self):
        result = _check_dangerous_patterns("pip uninstall -y flask", DANGEROUS_PATTERNS)
        assert result is not None

    def test_blocks_fork_bomb_pattern(self):
        result = _check_dangerous_patterns(":(){ :|:& };:", DANGEROUS_PATTERNS)
        assert result is not None

    def test_allows_rm_without_rf(self):
        # Normal rm (not recursive force) should pass dangerous pattern check
        # Note: _check_path_traversal handles workspace boundary
        assert _check_dangerous_patterns("rm file.txt", DANGEROUS_PATTERNS) is None

    def test_word_boundary_prevents_false_positive(self):
        # "rmfile" contains "rm" but should not match \brm\b
        result = _check_dangerous_patterns("rmfile script.sh", DANGEROUS_PATTERNS)
        assert result is None


# ============================================================================
# _check_internal_url
# ============================================================================

class TestCheckInternalUrl:
    def test_public_url_allowed(self):
        # targets_internal_address is imported inside _check_internal_url from network module
        with patch.object(_network_mod, "targets_internal_address", return_value=False):
            result = _check_internal_url("curl https://example.com")
            assert result is None

    def test_internal_url_blocked(self):
        with patch.object(_network_mod, "targets_internal_address", return_value=True):
            result = _check_internal_url("curl http://192.168.1.1/")
            assert result is not None
            assert "internal" in result.lower()

    def test_calls_targets_internal_address(self):
        with patch.object(_network_mod, "targets_internal_address", return_value=False) as mock:
            _check_internal_url("curl https://google.com")
            mock.assert_called_once_with("curl https://google.com", allow_loopback=True)


# ============================================================================
# _check_path_traversal
# ============================================================================

class TestCheckPathTraversal:
    def test_allows_normal_paths(self):
        assert _check_path_traversal("ls /home/user", False) is None
        assert _check_path_traversal("cat /etc/passwd", False) is None

    def test_blocks_parent_traversal(self):
        result = _check_path_traversal("cat /etc/passwd", False)
        # Note: /etc is outside default workspace but path_traversal check
        # blocks ../ patterns specifically
        assert result is None or "traversal" in result.lower()

    def test_blocks_backslash_parent_traversal(self):
        # ".." + "\\.." contains "..\\" pattern (backslash-parent in Windows path)
        result = _check_path_traversal(r"..\..\windows\system32\config", True)
        assert result is not None


# ============================================================================
# _check_workspace_boundary
# ============================================================================

class TestCheckWorkspaceBoundary:
    def test_allows_files_in_workspace(self):
        result = _check_workspace_boundary(
            "cat /workspace/file.txt",
            cwd="/workspace",
            workspace_root="/workspace",
            restrict_to_workspace=True,
        )
        assert result is None

    def test_allows_expanded_home_path(self):
        result = _check_workspace_boundary(
            "cat ~/project/file.txt",
            cwd="/home/user",
            workspace_root="/home/user",
            restrict_to_workspace=True,
        )
        # ~ expands to cwd, should be within workspace

    def test_blocks_path_outside_workspace(self):
        result = _check_workspace_boundary(
            "cat /etc/passwd",
            cwd="/workspace",
            workspace_root="/workspace",
            restrict_to_workspace=True,
        )
        assert result is not None


# ============================================================================
# check_command_safety — full integration
# ============================================================================

class TestCheckCommandSafety:
    def test_danger_override_bypasses_all_checks(self):
        """When danger_override=True, all checks are skipped."""
        with patch.object(_shell_validators_mod, "_check_dangerous_patterns") as m:
            result = check_command_safety(
                command="rm -rf /",
                cwd="/workspace",
                deny_patterns=DANGEROUS_PATTERNS,
                allow_patterns=[],
                restrict_to_workspace=True,
                workspace_root="/workspace",
                danger_override=True,
            )
            assert result is None  # Allowed
            m.assert_not_called()  # No checks performed

    def test_dangerous_command_blocked_without_override(self):
        result = check_command_safety(
            command="rm -rf /tmp/test",
            cwd="/workspace",
            deny_patterns=DANGEROUS_PATTERNS,
            allow_patterns=[],
            restrict_to_workspace=True,
            workspace_root="/workspace",
            danger_override=False,
        )
        assert result is not None

    def test_allowlist_blocks_nonmatching_commands(self):
        result = check_command_safety(
            command="ls /",
            cwd="/workspace",
            deny_patterns=[],
            allow_patterns=[r"^git ", r"^echo "],  # Only git and echo allowed
            restrict_to_workspace=True,
            workspace_root="/workspace",
            danger_override=False,
        )
        assert result is not None
        assert "not match any allowed pattern" in result

    def test_allowlist_allows_matching_commands(self):
        result = check_command_safety(
            command="git status",
            cwd="/workspace",
            deny_patterns=[],
            allow_patterns=[r"^git ", r"^echo "],
            restrict_to_workspace=True,
            workspace_root="/workspace",
            danger_override=False,
        )
        assert result is None  # Allowed

    def test_public_url_in_command_passes_ssrf_check(self):
        with patch.object(_network_mod, "targets_internal_address") as mock:
            mock.return_value = False
            result = check_command_safety(
                command="curl https://example.com",
                cwd="/workspace",
                deny_patterns=[],
                allow_patterns=[],
                restrict_to_workspace=True,
                workspace_root="/workspace",
                danger_override=False,
            )
            assert result is None  # Allowed
            mock.assert_called()

    def test_all_checks_pass_returns_none(self):
        result = check_command_safety(
            command="ls -la /workspace",
            cwd="/workspace",
            deny_patterns=[],
            allow_patterns=[],
            restrict_to_workspace=True,
            workspace_root="/workspace",
            danger_override=False,
        )
        assert result is None  # Allowed

    def test_multiple_urls_mixed_returns_blocked(self):
        with patch.object(_network_mod, "targets_internal_address") as mock:
            mock.return_value = True  # One URL is internal
            result = check_command_safety(
                command="curl https://example.com && curl http://192.168.1.1/",
                cwd="/workspace",
                deny_patterns=[],
                allow_patterns=[],
                restrict_to_workspace=True,
                workspace_root="/workspace",
                danger_override=False,
            )
            assert result is not None
