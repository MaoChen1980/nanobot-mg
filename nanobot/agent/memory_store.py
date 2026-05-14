"""MemoryStore — file I/O for memory files with SQLite delegation for history."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, truncate_text
from nanobot.agent.loop_utils import strip_think
from nanobot.agent.memory_vector import MemoryVectorIndex
from nanobot.utils.gitstore import GitStore

if TYPE_CHECKING:
    from nanobot.agent.db import NanobotDB


_RAW_ARCHIVE_MAX_CHARS = 16_000
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000
_HISTORY_ENTRY_HARD_CAP = 64_000


class MemoryStore:
    """File I/O for memory files: MEMORY.md, SOUL.md, USER.md.

    History and cursor operations are delegated to :class:`NanobotDB` when
    a *db* instance is provided.
    """

    _DEFAULT_MAX_HISTORY = 1000

    def __init__(
        self,
        workspace: Path,
        max_history_entries: int = _DEFAULT_MAX_HISTORY,
        db: NanobotDB | None = None,
    ):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self._db = db
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md",
        ])
        self.vector_index = MemoryVectorIndex(self.memory_dir)
        self.vector_index.load()

    @property
    def git(self) -> GitStore:
        return self._git

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # --- Categorized memory file support ---

    def list_memory_files(self) -> list[Path]:
        """Return all .md files under memory/ (excluding .vector_index/)."""
        return sorted(
            p for p in self.memory_dir.rglob("*.md")
            if ".vector_index" not in p.parts
        )

    def read_categorized_file(self, rel_path: str) -> str:
        """Read a file relative to memory/."""
        return self.read_file(self.memory_dir / rel_path)

    def write_categorized_file(self, rel_path: str, content: str) -> None:
        """Write a file relative to memory/, creating parent dirs."""
        target = self.memory_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def get_all_memory_text(self) -> str:
        """Concatenate all categorized memory files for full index rebuild."""
        parts: list[str] = []
        for f in self.list_memory_files():
            content = self.read_file(f)
            if content.strip():
                rel = f.relative_to(self.memory_dir)
                parts.append(f"--- {rel} ---\n{content}")
        return "\n\n".join(parts)

    def build_vector_index(self) -> None:
        """Rebuild the FAISS vector index from all memory files."""
        file_texts: dict[str, str] = {}
        for f in self.list_memory_files():
            content = self.read_file(f)
            if content.strip():
                rel = str(f.relative_to(self.memory_dir))
                file_texts[rel] = content
        if file_texts:
            self.vector_index.build_from_files(file_texts)
            self.vector_index.save()

    def append_history(self, entry: str, *, max_chars: int | None = None, timestamp: str | None = None) -> int:
        if self._db is None:
            return 0
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        content = strip_think(entry.rstrip())
        if len(content) > limit:
            content = truncate_text(content, limit)
        return self._db.append_history(content, timestamp=timestamp)

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        return self._db.read_unprocessed_history(since_cursor)

    def compact_history(self) -> None:
        if self.max_history_entries <= 0 or self._db is None:
            return
        self._db.compact_history(self.max_history_entries)

    def get_last_dream_cursor(self) -> int:
        if self._db is None:
            return 0
        return self._db.get_dream_cursor()

    def set_last_dream_cursor(self, cursor: int) -> None:
        if self._db is not None:
            self._db.set_dream_cursor(cursor)

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(self, messages: list[dict], *, max_chars: int | None = None) -> None:
        from nanobot.agent.memory_consolidator import Consolidator
        msgs = Consolidator._filter_archive_messages(messages)
        if not msgs:
            return
        limit = max_chars if max_chars is not None else _RAW_ARCHIVE_MAX_CHARS
        formatted = truncate_text(self._format_messages(msgs), limit)
        first_ts = msgs[0].get("timestamp", "unknown") if msgs else "unknown"
        last_ts = msgs[-1].get("timestamp", "unknown") if msgs else "unknown"
        record_ts = first_ts[:16] if first_ts != "unknown" else None
        self.append_history(
            f"[{first_ts} → {last_ts}] [RAW] {len(msgs)} messages\n"
            f"{formatted}",
            timestamp=record_ts,
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(msgs)
        )

    def update_summary(self, cursor: int, summary: str) -> None:
        if self._db is not None:
            self._db.update_summary(cursor, summary)
