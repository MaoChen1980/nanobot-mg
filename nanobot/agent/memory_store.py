"""MemoryStore — file I/O for memory files with SQLite delegation for history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, truncate_text
from nanobot.agent.loop_utils import strip_think
from nanobot.agent.memory_vector import MemoryVectorIndex
from nanobot.utils.gitstore import GitStore

if TYPE_CHECKING:
    from nanobot.agent.db import NanobotDB


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
        self.rules_file = workspace / "RULES.md"
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md", "RULES.md",
        ])
        self.vector_index = MemoryVectorIndex(self.memory_dir)
        if not self.vector_index.load() and self.list_memory_files():
            logger.info("No vector index found — building from existing memory/ files")
            self.build_vector_index()
        self.tasks_dir = workspace / "tasks"
        if self.tasks_dir.is_dir():
            self.tasks_index = MemoryVectorIndex(self.tasks_dir, index_dir=".tasks_index")
            if not self.tasks_index.load() and list(self._list_tasks_files()):
                logger.info("No tasks FAISS index found — building from existing tasks/ files")
                self.build_tasks_index()
        else:
            self.tasks_index = None

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

    def read_rules(self) -> str:
        return self.read_file(self.rules_file)

    def write_rules(self, content: str) -> None:
        self.rules_file.write_text(content, encoding="utf-8")

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # --- Categorized memory file support ---

    def list_memory_files(self) -> list[Path]:
        """Return all .md files under memory/ (excluding .vector_index/)."""
        return sorted(
            p for p in self.memory_dir.rglob("*.md")
            if ".vector_index" not in p.parts and p.name not in ("index.md", "MEMORY.md")
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
        """Rebuild the FAISS vector index (incremental if index exists)."""
        self.vector_index.build_incremental()

    def _list_tasks_files(self) -> list[Path]:
        """Return all .md files under tasks/ (excluding .tasks_index/)."""
        if not self.tasks_dir.is_dir():
            return []
        return sorted(
            p for p in self.tasks_dir.rglob("*.md")
            if ".tasks_index" not in p.parts
        )

    def build_tasks_index(self) -> None:
        """Rebuild the FAISS index from all tasks/ files (incremental if exists)."""
        if self.tasks_index is not None:
            self.tasks_index.build_incremental()

    def condense_session_to_history(self, messages: list[dict]) -> int:
        """Archive session messages into history, grouped by turns.

        Each turn is condensed to: user input -> thinking/tool_names -> final
        response.  Tool results are excluded (large and already digested).
        Returns number of turns archived.
        """
        if not messages or self._db is None:
            return 0

        # Group consecutive messages into user-started turns
        turns: list[list[dict]] = []
        current: list[dict] = []
        for msg in messages:
            if msg.get("status") == "synthetic":
                continue
            if msg.get("role") == "user" and current:
                turns.append(current)
                current = []
            current.append(msg)
        if current:
            turns.append(current)

        archived = 0
        for turn_msgs in turns:
            user_msg = turn_msgs[0]
            if user_msg.get("role") != "user":
                continue

            parts: list[str] = []
            raw_content = user_msg.get("content") or ""
            if isinstance(raw_content, (list, dict)):
                raw_content = json.dumps(raw_content, ensure_ascii=False)
            user_text = raw_content.strip()
            if user_text:
                parts.append(f"User: {user_text}")

            thinking: list[str] = []
            tool_names: list[str] = []
            final_response = ""
            for msg in turn_msgs:
                if msg.get("role") != "assistant":
                    continue
                for b in (msg.get("thinking_blocks") or []):
                    if isinstance(b, dict) and b.get("thinking"):
                        thinking.append(b["thinking"])
                rc = msg.get("reasoning_content")
                if isinstance(rc, str) and rc:
                    thinking.append(rc)
                for tc in (msg.get("tool_calls") or []):
                    if isinstance(tc, dict):
                        name = tc.get("function", {}).get("name", "")
                        if name and name not in tool_names:
                            tool_names.append(name)
                raw_content = msg.get("content") or ""
                if isinstance(raw_content, (list, dict)):
                    raw_content = json.dumps(raw_content, ensure_ascii=False)
                c = raw_content.strip()
                if c:
                    final_response = c

            if thinking:
                joined = " ".join(thinking)
                if len(joined) > 500:
                    joined = joined[:500] + "..."
                parts.append(f"Thinking: {joined}")
            if tool_names:
                parts.append(f"Tools: {', '.join(tool_names)}")
            if final_response:
                parts.append(f"Assistant: {final_response}")

            content = "\n\n".join(parts)
            if content.strip():
                self.append_history(content, timestamp=user_msg.get("timestamp"))
                archived += 1

        if archived:
            total_msgs = len(messages)
            logger.info("history: archived {} turns ({} msgs) from session — consider N=100/M=20 trim", archived, total_msgs)
        return archived

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

    def update_summary(self, cursor: int, summary: str) -> None:
        if self._db is not None:
            self._db.update_summary(cursor, summary)

    def get_last_extractor_cursor(self) -> int:
        if self._db is None:
            return 0
        return self._db.get_extractor_cursor()

    def set_last_extractor_cursor(self, cursor: int) -> None:
        if self._db is not None:
            self._db.set_extractor_cursor(cursor)
