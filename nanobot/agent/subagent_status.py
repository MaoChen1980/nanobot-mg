"""Subagent status dataclass."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None


def format_partial_progress(result) -> str:
    """Format partial progress for error announcements."""
    completed = [e for e in result.tool_events if e["status"] == "ok"]
    failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
    lines: list[str] = []
    if completed:
        lines.append("Completed steps:")
        for event in completed[-3:]:
            lines.append(f"- {event['name']}: {event['detail']}")
    if failure:
        if lines:
            lines.append("")
        lines.append("Failure:")
        lines.append(f"- {failure['name']}: {failure['detail']}")
    if result.error and not failure:
        if lines:
            lines.append("")
        lines.append("Failure:")
        lines.append(f"- {result.error}")
    return "\n".join(lines) or (result.error or "Error: subagent execution failed.")