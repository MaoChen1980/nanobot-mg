"""Shell command validators for ExecTool security checks."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.agent.tools.danger import danger_warning

if TYPE_CHECKING:
    pass

# Dangerous command patterns that should be blocked.
# Each pattern must be specific enough to avoid false positives
# in legitimate commands (e.g. git commit messages, filenames).
DANGEROUS_PATTERNS: list[str] = [
    r"\brm\s+-(?:rf|fr)\b",          # rm -rf / rm -fr (recursive + force, not solo -f or -r)
    r"\bdel\s+/[fq]\b",              # del /f, del /q (Windows force/quiet)
    r"\brmdir\s+/s\b",               # rmdir /s (Windows recursive)
    r"(?:^|[;&|]\s*)format\b(?!-)",  # format as standalone command (exclude PowerShell Format-* cmdlets)
    r"\b(mkfs|diskpart)\b",          # disk operations
    r"\bdd\s+if=",                   # dd input redirection
    r">\s*/dev/sd",                  # write to block device
    r"(?:^|[;&|]\s*)shutdown\b",  # system power — command-start only (not in args/messages)
    r"(?:^|[;&|]\s*)reboot\b",    # system reboot — command-start only
    r"(?:^|[;&|]\s*)poweroff\b",  # system power-off — command-start only
    r":\(\)\s*\{.*\};\s*:",       # fork bomb
    r"(?:^|[;&|]\s*)git\s+push\s+--force\b",  # force push — loses remote history
    r"(?:^|[;&|]\s*)git\s+reset\s+--hard\b",  # hard reset — loses uncommitted changes
    r"(?:^|[;&|]\s*)git\s+clean\s+-f",        # clean — deletes untracked files
    r"(?:^|[;&|]\s*)pip\s+uninstall\b",       # uninstall packages
]


def _check_dangerous_patterns(command: str, deny_patterns: list[str]) -> str | None:
    """Check for dangerous command patterns. Returns error message or None."""
    lower = command.strip().lower()
    for pattern in deny_patterns:
        if re.search(pattern, lower):
            return "Error: Command blocked by safety guard (dangerous pattern detected)"
    return None


def _check_internal_url(command: str) -> str | None:
    """Check for internal/private URLs. Returns error message or None.

    Loopback (localhost) is allowed — the agent often needs to reach
    services it spawned locally (health checks, dev servers).
    """
    from nanobot.security.network import targets_internal_address
    if targets_internal_address(command, allow_loopback=True):
        return "Error: Command blocked by safety guard (internal/private URL detected)"
    return None


def _check_path_traversal(command: str, restrict_to_workspace: bool) -> str | None:
    """Check for path traversal attempts. Returns error message or None."""
    if restrict_to_workspace:
        if "..\\" in command or "../" in command:
            return "Error: Command blocked by safety guard (path traversal detected)"
    return None


def _check_workspace_boundary(
    command: str,
    cwd: str,
    workspace_root: str | None,
    restrict_to_workspace: bool,
) -> str | None:
    """Check if command accesses paths outside allowed workspace. Returns error message or None."""
    if not restrict_to_workspace or not workspace_root:
        return None

    cwd_path = Path(cwd).resolve()
    workspace = Path(workspace_root).resolve()

    win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
    posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command)
    home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command)

    media_path = Path(os.environ.get("MEDIA_DIR", tempfile.gettempdir())).resolve()

    for raw in win_paths + posix_paths + home_paths:
        try:
            expanded = os.path.expandvars(raw.strip())
            p = Path(expanded).expanduser().resolve()
        except Exception:
            continue

        if (
            p.is_absolute()
            and cwd_path not in p.parents
            and p != cwd_path
            and workspace not in p.parents
            and p != workspace
            and media_path not in p.parents
            and p != media_path
        ):
            return "Error: Command blocked by safety guard (path outside working dir)"

    return None


def check_command_safety(
    command: str,
    cwd: str,
    deny_patterns: list[str],
    allow_patterns: list[str],
    restrict_to_workspace: bool,
    workspace_root: str | None,
    danger_override: bool = False,
) -> str | None:
    """Validate a shell command against all security checks.

    When *danger_override* is True, all checks are skipped — use only when
    the LLM has acknowledged the risk and explicitly confirmed.

    Returns None if command is allowed, or a warning string if blocked.
    """
    if danger_override:
        return None

    cmd = command.strip()
    lower = cmd.lower()

    error = _check_dangerous_patterns(cmd, deny_patterns)
    if error:
        return danger_warning(
            problem=f"Command matches a dangerous pattern: {command.strip()[:120]}",
            risk="Potential data loss, system damage, or security breach",
            suggestion="Consider using dedicated file/edit/delete tools instead of raw shell commands. "
                       "If you must proceed, ensure you have a rollback plan (e.g., git commit first)",
            tool_name="exec",
        )

    if allow_patterns:
        if not any(re.search(p, lower) for p in allow_patterns):
            return danger_warning(
                problem="Command does not match any allowed pattern",
                risk="The command is not in the configured allowlist",
                suggestion="Use a command that matches one of the allowed patterns",
                tool_name="exec",
            )

    error = _check_internal_url(cmd)
    if error:
        return danger_warning(
            problem="Command targets an internal or private network address",
            risk="May accidentally access or expose internal services",
            suggestion="Use web_fetch for HTTP requests — it has built-in SSRF protection",
            tool_name="exec",
        )

    error = _check_path_traversal(cmd, restrict_to_workspace)
    if error:
        return danger_warning(
            problem="Command contains path traversal (../)",
            risk="May access files outside the allowed workspace",
            suggestion="Use absolute paths within the workspace",
            tool_name="exec",
        )

    error = _check_workspace_boundary(cmd, cwd, workspace_root, restrict_to_workspace)
    if error:
        return danger_warning(
            problem="Command accesses paths outside the allowed workspace",
            risk="May modify files outside the project directory",
            suggestion="Use paths within the workspace",
            tool_name="exec",
        )

    return None