"""Danger detection utilities — shared warning format and common checks.

Tools detect dangerous operations and return warnings (not errors) so the LLM
can reconsider the risk and choose to override with ``danger_override=true``.
"""

from __future__ import annotations

from pathlib import Path


def danger_warning(
    problem: str,
    risk: str,
    suggestion: str = "",
    tool_name: str = "this tool",
) -> str:
    """Format a standardized danger warning.

    The returned string does *not* start with ``"Error"``, so the framework
    treats it as a normal tool result.  The LLM reads the warning, evaluates
    the risk, and can call again with ``danger_override=true`` to proceed.
    """
    lines = [f"⚠️ Danger: {problem}", f"  Risk: {risk}"]
    if suggestion:
        lines.append(f"  Suggestion: {suggestion}")
    lines.append(
        f"  To proceed anyway, re-call {tool_name} with danger_override=true"
    )
    return "\n".join(lines)


def check_overwrite_danger(fp: Path, was_read: bool, size_bytes: int) -> tuple[bool, str]:
    """Check whether overwriting *fp* is potentially dangerous.

    Returns ``(True, reason)`` when the file exists, was not read, and is
    larger than 1 KB — suggesting the LLM hasn't verified the content.
    Returns ``(False, "")`` otherwise.
    """
    if not fp.exists():
        return False, ""
    if not was_read and size_bytes > 1024:
        return (
            True,
            f"overwriting {fp.name} ({size_bytes} bytes) without reading it first "
            f"— content may not be what you expect",
        )
    return False, ""
