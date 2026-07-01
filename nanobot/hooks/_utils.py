"""Shared utilities for nanobot hooks."""

from __future__ import annotations

from pathlib import Path


RESOLVED_FILE = Path.home() / ".nanobot" / "self_improve" / "resolved_findings.jsonl"


def read_resolved_ids(max_ids: int | None = None) -> set[str]:
    """Read resolved finding IDs from JSONL file (one ID per line).

    Args:
        max_ids: If None, read ALL lines (no truncation). Previously defaulted to 200,
            but truncation caused old resolved IDs to be forgotten, making resolved
            findings re-appear as new. Set to a finite number only in test contexts.
    """
    if not RESOLVED_FILE.exists():
        return set()
    ids: set[str] = set()
    try:
        lines = RESOLVED_FILE.read_text(encoding="utf-8").strip().splitlines()
        # Respect max_ids cap when explicitly set (e.g. in tests); default = read all
        if max_ids is not None:
            lines = lines[-max_ids:]
        for line in lines:
            line = line.strip()
            if line:
                ids.add(line)
    except OSError:
        pass
    return ids
