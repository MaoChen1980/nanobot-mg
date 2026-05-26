"""Track file-read state for read-before-edit warnings and read deduplication."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float
    offset: int
    limit: int | None
    content_hash: str | None
    can_dedup: bool


class FileStateManager:
    """Manages file read state for read-before-edit warnings and deduplication.

    This class encapsulates what was previously module-level global state,
    allowing multiple independent workspaces or test contexts.
    """

    def __init__(self) -> None:
        self._state: dict[str, ReadState] = {}

    def _hash_file(self, p: str) -> str | None:
        try:
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except OSError:
            return None

    def record_read(self, path: str | Path, offset: int = 1, limit: int | None = None) -> None:
        """Record that a file was read (called after successful read)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=offset,
            limit=limit,
            content_hash=self._hash_file(p),
            can_dedup=True,
        )

    def record_write(self, path: str | Path) -> None:
        """Record that a file was written (updates mtime in state)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._state.pop(p, None)
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=1,
            limit=None,
            content_hash=self._hash_file(p),
            can_dedup=False,
        )

    def check_read(self, path: str | Path) -> str | None:
        """Check if a file has been read and is fresh.

        Returns None if OK, or a warning string.
        When mtime changed but file content is identical (e.g. touch, editor save),
        the check passes to avoid false-positive staleness warnings.
        """
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return "Warning: file has not been read yet. Read it first to verify content before editing."
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return None
        if current_mtime != entry.mtime:
            if entry.content_hash and self._hash_file(p) == entry.content_hash:
                entry.mtime = current_mtime
                return None
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        # mtime unchanged - still check content hash to detect quick modifications
        if entry.content_hash and self._hash_file(p) != entry.content_hash:
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        return None

    def is_unchanged(self, path: str | Path, offset: int = 1, limit: int | None = None) -> bool:
        """Return True if file was previously read with same params and content is unchanged."""
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return False
        if not entry.can_dedup:
            return False
        if entry.offset != offset or entry.limit != limit:
            return False
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return False
        if current_mtime != entry.mtime:
            current_hash = self._hash_file(p)
            if current_hash != entry.content_hash:
                entry.can_dedup = False
                return False
            entry.can_dedup = False
            return True
        return True

    def check_content_hash(self, path: str | Path) -> str | None:
        """Return a warning if file content has changed since last read.

        Returns None if file unchanged, or a warning string with diff info.
        This uses the full-file SHA256 stored by record_read().
        """
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return "Warning: file has not been read yet. Read it first to verify content before editing."
        if entry.content_hash is None:
            return None  # No hash recorded — no check possible
        current_hash = self._hash_file(p)
        if current_hash is None:
            return None
        if current_hash == entry.content_hash:
            return None
        # File changed — return warning with details
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        return (
            f"Warning: file content has changed since last read "
            f"(expected hash: {entry.content_hash[:16]}..., current hash: {current_hash[:16]}..., "
            f"size: {size} bytes). "
            f"Use read_file to refresh content before editing."
        )

    def get_content_hash(self, path: str | Path) -> str | None:
        """Return the content hash stored for this file (from last read)."""
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        return entry.content_hash if entry else None

    def clear(self) -> None:
        """Clear all tracked state (useful for testing)."""
        self._state.clear()


# Module-level functions for backward compatibility


def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


# Default manager instance for backward compatibility
_default_manager = FileStateManager()


def record_read(path: str | Path, offset: int = 1, limit: int | None = None) -> None:
    """Record that a file was read (called after successful read)."""
    return _default_manager.record_read(path, offset, limit)


def record_write(path: str | Path) -> None:
    """Record that a file was written (updates mtime in state)."""
    return _default_manager.record_write(path)


def check_read(path: str | Path) -> str | None:
    """Check if a file has been read and is fresh."""
    return _default_manager.check_read(path)


def is_unchanged(path: str | Path, offset: int = 1, limit: int | None = None) -> bool:
    """Return True if file was previously read with same params and content is unchanged."""
    return _default_manager.is_unchanged(path, offset, limit)


def check_content_hash(path: str | Path) -> str | None:
    """Return a warning if file content has changed since last read."""
    return _default_manager.check_content_hash(path)


def get_content_hash(path: str | Path) -> str | None:
    """Return the content hash stored for this file (from last read)."""
    return _default_manager.get_content_hash(path)


def clear() -> None:
    """Clear all tracked state (useful for testing)."""
    _default_manager.clear()
