"""Log event tool — append timestamped events to memory/events/{topic}.md."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory_store import MemoryStore
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

_SESSION_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename component (no slashes)."""
    safe = _SESSION_KEY_RE.sub("_", name)
    return safe[:64].strip("_")


@tool_parameters(
    build_parameters_schema(
        topic=p(
            "string",
            "Event topic/category — used as the filename and section heading. "
            "Use kebab-case, e.g. 'trading-backtest', 'project-alpha', 'user-preferences'.",
        ),
        summary=p(
            "string",
            "One-line summary of what happened. Concise, past tense, actionable.",
            maxLength=200,
        ),
        detail=p(
            "string",
            "Optional additional context — why it matters, what was decided, follow-up needed.",
            default="",
            maxLength=1000,
        ),
        required=["topic", "summary"],
    ),
)
class LogEventTool(Tool):
    """Append a timestamped event to ``memory/events/{topic}.md``.

    Events form an append-only timeline under a ``## Timeline`` section.
    Use for significant occurrences: decisions, user preferences, bug fixes,
    project milestones, health changes — anything worth recalling later.

    This tool handles file creation, format, dedup, and sorting.
    You only need to decide *what* is worth recording.
    """

    def __init__(self, store: MemoryStore):
        self._store = store
        self._events_dir = Path(store.workspace) / "memory" / "events"
    instruction = "Record a decision, preference change, bug fix, milestone, or significant event for future reference. Do NOT use for routine operations or temporary state."

    name = "log_event"
    read_only = False

    description = (
        "Record a timestamped event to memory/events/{topic}.md. "
        "Handles file creation, dedup (by summary + detail), and date sorting. "
        "Events are appended to a ## Timeline section. "
        "NOT indexed by FAISS — use read_file to access."
    )

    async def execute(self, topic: str, summary: str, detail: str = "", **kwargs: Any) -> str:
        topic = topic.strip()
        summary = summary.strip()
        detail = detail.strip()

        if not topic or not summary:
            return "Error: topic and summary are required."

        safe_name = _sanitize_filename(topic.replace("/", "_"))
        path = self._events_dir / f"{safe_name}.md"

        async with self._store.events_lock:
            # Read existing timeline entries
            existing_entries: list[str] = []
            if path.exists():
                text = path.read_text(encoding="utf-8")
                in_timeline = False
                for line in text.split("\n"):
                    s = line.strip()
                    if s == "## Timeline":
                        in_timeline = True
                        continue
                    if in_timeline:
                        if s.startswith("## ") or s.startswith("---"):
                            break
                        if s.startswith("- "):
                            existing_entries.append(s)

            # Collect existing (summary, detail) pairs for dedup
            existing_dedup_keys: set[str] = set()
            for entry in existing_entries:
                colon = entry.find(": ")
                if colon > 0:
                    rest = entry[colon + 2:].strip()
                    existing_dedup_keys.add(rest)

            # Build dedup key from summary (and detail when present)
            dedup_key = summary
            if detail:
                dedup_key += f" — {detail}"

            if dedup_key in existing_dedup_keys:
                return f"Skipped: duplicate event already exists in events/{safe_name}.md"

            # Build new entry
            today = datetime.now().strftime("%Y-%m-%d")
            new_entry = f"- {today}: {summary}"
            if detail:
                new_entry += f" — {detail}"

            # Derive heading from topic
            heading = topic.replace("/", " / ").replace("-", " ").title()

            # Merge and sort
            all_entries = existing_entries + [new_entry]
            all_entries.sort(key=lambda x: x[2:12] if x.startswith("- ") else "")

            # Write
            self._events_dir.mkdir(parents=True, exist_ok=True)
            content = (
                f"# {heading}\n\n"
                "## Timeline\n\n"
                + "\n".join(all_entries)
                + "\n\n---\n"
            )
            path.write_text(content, encoding="utf-8")

        return f"Logged event to events/{safe_name}.md: {summary}"