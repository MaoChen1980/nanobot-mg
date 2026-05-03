"""Shell command validators for ExecTool security checks."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.security.network import contains_internal_url

# Dangerous command patterns that should be blocked
DANGEROUS_PATTERNS: list[str] = [
    r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
    r"\bdel\s+/[fq]\b",              # del /f, del /q
    r"\brmdir\s+/s\b",               # rmdir /s
    r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
    r"\b(mkfs|diskpart)\b",          # disk operations
    r"\bdd\s+if=",                   # dd
    r">\s*/dev/sd",                  # write to disk
    r"\b(shutdown|reboot|poweroff)\b",  # system power
    r":\(\)\s*\{.*\};\s*:",          # fork bomb
]

# Nanobot internal files that should not be modified
NANOBOT_FILES: list[str] = [
    "history.jsonl",
    ".dream_cursor",
]


def _check_dangerous_patterns(command: str, deny_patterns: list[str]) -> str | None:
    """Check for dangerous command patterns. Returns error message or None."""
    lower = command.strip().lower()
    for pattern in deny_patterns:
        if re.search(pattern, lower):
            return "Error: Command blocked by safety guard (dangerous pattern detected)"
    return None


def _check_internal_url(command: str) -> str | None:
    """Check for internal/private URLs. Returns error message or None."""
    from nanobot.security.network import contains_internal_url
    if contains_internal_url(command):
        return "Error: Command blocked by safety guard (internal/private URL detected)"
    return None


def _check_nanobot_files(command: str) -> str | None:
    """Check for commands targeting nanobot internal files. Returns error message or None."""
    lower = command.lower()
    for filename in NANOBOT_FILES:
        if re.search(r">>?\s*\S*" + re.escape(filename), lower):
            return f"Error: Command blocked by safety guard (nanobot file '{filename}' redirect)"
        if re.search(r"\btee\b[^|;&<>]*(?:" + re.escape(filename) + r")", lower):
            return f"Error: Command blocked by safety guard (nanobot file '{filename}' via tee)"
        if re.search(r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:" + re.escape(filename) + r")", lower):
            return f"Error: Command blocked by safety guard (nanobot file '{filename}' via cp/mv)"
        if re.search(r"\bdd\b[^|;&<>]*\bof=\S*(?:" + re.escape(filename) + r")", lower):
            return f"Error: Command blocked by safety guard (nanobot file '{filename}' via dd)"
        if re.search(r"\bsed\s+-i[^|;&<>]*(?:" + re.escape(filename) + r")", lower):
            return f"Error: Command blocked by safety guard (nanobot file '{filename}' via sed -i)"
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

    media_path = Path(os.environ.get("MEDIA_DIR", "/tmp")).resolve()

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


def validate_command(
    command: str,
    cwd: str,
    deny_patterns: list[str],
    allow_patterns: list[str],
    restrict_to_workspace: bool,
    workspace_root: str | None,
) -> str | None:
    """Validate a shell command against all security checks.

    Returns None if command is allowed, or an error message if blocked.
    """
    cmd = command.strip()
    lower = cmd.lower()

    error = _check_dangerous_patterns(cmd, deny_patterns)
    if error:
        return error

    if allow_patterns:
        if not any(re.search(p, lower) for p in allow_patterns):
            return "Error: Command blocked by safety guard (not in allowlist)"

    error = _check_internal_url(cmd)
    if error:
        return error

    error = _check_path_traversal(cmd, restrict_to_workspace)
    if error:
        return error

    error = _check_workspace_boundary(cmd, cwd, workspace_root, restrict_to_workspace)
    if error:
        return error

    error = _check_nanobot_files(cmd)
    if error:
        return error

    return None