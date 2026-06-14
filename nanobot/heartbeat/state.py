"""Persistent task run state for heartbeat — tracks last-run timestamps."""

from __future__ import annotations

import json
import time
from pathlib import Path


class HeartbeatState:
    """Tracks last-run timestamps for interval tasks in TREE.md.

    Persisted as ``.heartbeat_state.json`` in the tasks directory.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                self._data = json.loads(raw)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def last_run(self, task_name: str) -> float | None:
        """Return last-run timestamp for *task_name*, or None if never run."""
        return self._data.get(task_name)

    def mark_run(self, task_name: str, ts: float | None = None) -> None:
        """Record a run for *task_name* at *ts* (default: now)."""
        self._data[task_name] = ts or time.time()
        self._save()

    def mark_tasks(self, timestamps: dict[str, float]) -> None:
        """Batch-record timestamps for multiple tasks."""
        self._data.update(timestamps)
        self._save()

    @property
    def all(self) -> dict[str, float]:
        return dict(self._data)
