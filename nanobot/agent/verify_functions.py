"""Hypothesis verification functions for goal constraint checking."""

from __future__ import annotations

from typing import Any


# Operation type mapping for constraint checking
OPERATION_TYPES: dict[str, list[str]] = {
    "read": ["read_file", "grep", "glob", "list_dir"],
    "write": ["write_file", "edit_file"],
    "delete": ["delete_file", "rm"],
    "execute": ["exec", "run_command"],
    "network": ["send_message", "http_request"],
}


class VerifyResult:
    """Result of a constraint verification check."""

    def __init__(self, approved: bool, reason: str | None = None):
        self.approved = approved
        self.reason = reason


def check_operation_allowed(tool_name: str, constraints: list[str]) -> bool:
    """Check if a tool operation is allowed under the given constraints.

    Args:
        tool_name: Name of the tool being called
        constraints: List of operation constraints (e.g. ["no_delete", "read_only"])

    Returns:
        True if the operation is allowed, False otherwise
    """
    tool_op = None
    for op, tools in OPERATION_TYPES.items():
        if tool_name in tools:
            tool_op = op
            break

    if tool_op is None:
        return True  # Unknown tool, default allow

    for constraint in constraints:
        if constraint == "read_only" and tool_op not in ["read"]:
            return False
        if constraint == "no_delete" and tool_op == "delete":
            return False

    return True


def verify_action(
    goal_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    goal_scope: dict[str, Any] | None = None,
) -> VerifyResult:
    """Verify if a tool action is allowed under the goal's structural constraints.

    Args:
        goal_id: The goal ID for logging/debugging
        tool_name: Name of the tool being called
        arguments: Tool arguments
        goal_scope: The goal's scope data containing structural_constraints

    Returns:
        VerifyResult with approved=True if allowed, approved=False with reason if blocked
    """
    if not goal_scope:
        return VerifyResult(approved=True)

    constraints = goal_scope.get("structural_constraints", {})
    if not constraints:
        return VerifyResult(approved=True)

    # Check operation_constraints
    operation_constraints = constraints.get("operation_constraints", [])
    if operation_constraints:
        if not check_operation_allowed(tool_name, operation_constraints):
            allowed = _format_allowed_operations(operation_constraints)
            return VerifyResult(
                approved=False,
                reason=f"[BLOCKED] Operation '{tool_name}' not allowed. Allowed: {allowed}",
            )

    # Check file_patterns (allow only matching paths)
    file_patterns = constraints.get("file_patterns", [])
    if file_patterns:
        path = _extract_path_argument(tool_name, arguments)
        if path and not _matches_any_pattern(path, file_patterns):
            return VerifyResult(
                approved=False,
                reason=f"[BLOCKED] Path '{path}' not in allowed patterns: {file_patterns}",
            )

    # Check deny_patterns
    deny_patterns = constraints.get("deny_patterns", [])
    if deny_patterns:
        path = _extract_path_argument(tool_name, arguments)
        if path and _matches_any_pattern(path, deny_patterns):
            return VerifyResult(
                approved=False,
                reason=f"[BLOCKED] Path '{path}' matches deny pattern",
            )

    # Check api_blacklist
    api_blacklist = constraints.get("api_blacklist", [])
    if tool_name in api_blacklist:
        return VerifyResult(
            approved=False,
            reason=f"[BLOCKED] API '{tool_name}' is blacklisted",
        )

    return VerifyResult(approved=True)


def _extract_path_argument(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Extract file path from tool arguments."""
    path_keys = ["path", "file", "file_path", "target", "destination"]
    for key in path_keys:
        if key in arguments:
            return arguments[key]
    return None


def _matches_any_pattern(path: str, patterns: list[str]) -> bool:
    """Check if path matches any of the given patterns."""
    import fnmatch

    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        # Also check if pattern is a prefix of the path
        if path.startswith(pattern.rstrip("*")):
            return True
    return False


def _format_allowed_operations(constraints: list[str]) -> str:
    """Format allowed operations for error message."""
    allowed = []
    if "read_only" in constraints:
        allowed.append("read operations (read_file, grep, glob, list_dir)")
    if "no_delete" in constraints:
        allowed.append("no delete operations")
    return ", ".join(allowed) if allowed else "read operations only"