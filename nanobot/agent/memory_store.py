"""MemoryStore — file I/O for memory files with SQLite delegation for history."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, truncate_text
from nanobot.agent.memory_vector import MemoryVectorIndex
from nanobot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from nanobot.utils.gitstore import GitStore

if TYPE_CHECKING:
    from nanobot.agent.db import NanobotDB


_HISTORY_ENTRY_HARD_CAP = 64_000

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _extract_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a YAML field from markdown frontmatter via safe YAML parse."""
    m = _FRONTMATTER_RE.search(content)
    if not m:
        return None
    try:
        import yaml

        parsed = yaml.safe_load(m.group(1))
        if isinstance(parsed, dict) and isinstance(parsed.get(field), str):
            return parsed[field].strip()
    except Exception:
        pass
    return None


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
        self.events_lock = asyncio.Lock()
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md", "RULES.md",
        ])
        # mtime-based file read cache: path_key -> (mtime, content)
        self._file_cache: dict[str, tuple[float, str]] = {}
        self.vector_index = MemoryVectorIndex(self.memory_dir)
        self.vector_index.load()
        self._memory_index_built = False
        self.tasks_dir = workspace / "tasks"
        if self.tasks_dir.is_dir():
            self.tasks_index = MemoryVectorIndex(self.tasks_dir, index_dir=".tasks_index")
            self.tasks_index.load()
            self._tasks_index_built = False
        else:
            self.tasks_index = None
            self._tasks_index_built = True
        self.skills_loader = SkillsLoader(workspace)
        self.skills_index = MemoryVectorIndex(self.memory_dir, index_dir=".skills_index")
        self.skills_index.load()
        self._last_skills_rebuild: float = 0.0
        self._skills_index_built = False

    @property
    def git(self) -> GitStore:
        return self._git

    def read_file(self, path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
            cached = self._file_cache.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
            content = path.read_text(encoding="utf-8")
            self._file_cache[str(path)] = (mtime, content)
            return content
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

    def ensure_memory_index(self) -> None:
        """Lazily build the memory FAISS index if not already built."""
        if self._memory_index_built:
            return
        if self.vector_index._index is not None and self.vector_index._chunks:
            self._memory_index_built = True
            return
        if not self.list_memory_files():
            self._memory_index_built = True
            return
        logger.info("No vector index found — building from existing memory/ files")
        self.build_vector_index()

    def build_vector_index(self) -> None:
        """Full rebuild of the FAISS vector index from all memory files."""
        file_texts: dict[str, str] = {}
        for f in self.list_memory_files():
            content = self.read_file(f)
            if content.strip():
                rel = f.relative_to(self.memory_dir).as_posix()
                file_texts[rel] = content
        self.vector_index.build_from_files(file_texts)
        self._memory_index_built = True

    def _list_tasks_files(self) -> list[Path]:
        """Return all .md files under tasks/ (excluding .tasks_index/)."""
        if not self.tasks_dir.is_dir():
            return []
        return sorted(
            p for p in self.tasks_dir.rglob("*.md")
            if ".tasks_index" not in p.parts
        )

    def ensure_tasks_index(self) -> None:
        """Lazily build the tasks FAISS index if not already built."""
        if self._tasks_index_built:
            return
        if self.tasks_index is None:
            self._tasks_index_built = True
            return
        if self.tasks_index._index is not None and self.tasks_index._chunks:
            self._tasks_index_built = True
            return
        if not self._list_tasks_files():
            self._tasks_index_built = True
            return
        logger.info("No tasks FAISS index found — building from existing tasks/ files")
        self.build_tasks_index()

    def build_tasks_index(self) -> None:
        """Full rebuild of the tasks FAISS index from all tasks files."""
        if self.tasks_index is None:
            return
        file_texts: dict[str, str] = {}
        for f in self._list_tasks_files():
            content = self.read_file(f)
            if content.strip():
                rel = f.relative_to(self.tasks_dir).as_posix()
                file_texts[rel] = content
        self.tasks_index.build_from_files(file_texts)
        self._tasks_index_built = True

    def ensure_skills_index(self) -> None:
        """Lazily build the skills FAISS index if not already built."""
        if self._skills_index_built:
            return
        if self.skills_index._index is not None and self.skills_index._chunks:
            self._skills_index_built = True
            return
        if not self.skills_loader.list_skills(filter_unavailable=False):
            self._skills_index_built = True
            return
        logger.info("No skills index found — building from SKILL.md files")
        self.build_skills_index()

    def build_skills_index(self) -> None:
        """Full rebuild of the skills FAISS index from frontmatter metadata.

        Indexes each skill as ``{name}: {description}  {path}`` (from SKILL.md frontmatter).
        The path is the real file path so LLM can ``read_file`` to load the full skill.
        Scans workspace and built-in dirs independently so built-in skills are
        always indexed regardless of shadowing.

        Also auto-classifies skills missing a ``category:`` field by embedding
        their description against existing category centroids (threshold 0.55).
        Only matches against *existing* categories — no generic catch-all bucket.
        Skills that don't fit any existing category stay uncategorized, prompting
        the LLM (via warning banner) to create a meaningful grouping.

        This supports two use cases:
        1. **skill_search tool** — LLM queries to find applicable skills for the task.
        2. **Dedup during skill creation** — detect existing coverage before creating.
        """
        file_texts: dict[str, str] = {}
        skills_data: list[dict[str, Any]] = []
        for root in (self.skills_loader.workspace_skills, BUILTIN_SKILLS_DIR):
            if root.exists():
                for d in sorted(root.iterdir()):
                    skill_file = d / "SKILL.md"
                    if d.is_dir() and skill_file.exists():
                        key = f"skills/{d.name}.md"
                        if key not in file_texts:
                            content = skill_file.read_text(encoding="utf-8")
                            name = d.name
                            desc = _extract_frontmatter_field(content, "description") or name
                            cat = _extract_frontmatter_field(content, "category")
                            skills_data.append({
                                "name": name, "desc": desc, "category": cat,
                                "file": skill_file, "key": key, "content": content,
                            })
                            file_texts[key] = f"{name}: {desc}  {skill_file}"
        self._auto_classify_skills(skills_data)
        # Auto-classification may have modified SKILL.md frontmatter — invalidate
        # the skills-loader cache so downstream build_skills_summary() picks up
        # the new category fields.
        self.skills_loader._list_cache = None
        self.skills_index.build_from_files(file_texts)
        self._last_skills_rebuild = time.time()
        self._skills_index_built = True

    def _auto_classify_skills(self, skills_data: list[dict[str, Any]]) -> None:
        """Auto-classify uncategorized skills using embedding similarity.

        Builds per-category centroids from already-categorized skills, then
        assigns the nearest category to each uncategorized skill whose cosine
        similarity meets the threshold.  Updates SKILL.md frontmatter in place.

        Only matches against *existing* categories — skills that don't fit any
        existing category remain uncategorized (shown as "Other" in the summary
        for LLM attention).
        """
        categorized = [s for s in skills_data if s.get("category")]
        uncategorized = [s for s in skills_data if not s.get("category")]
        if not uncategorized:
            return

        cat_texts: dict[str, list[str]] = {}
        for s in categorized:
            cat = s["category"]
            if cat:
                cat_texts.setdefault(cat, []).append(s["desc"])

        if not cat_texts:
            logger.info("Auto-classify: no categorized skills to build centroids")
            return

        model = self.skills_index._model
        if model is None:
            if not self.skills_index._load_model():
                logger.info("Auto-classify skipped: no embedding model")
                return
            model = self.skills_index._model

        try:
            import numpy as np
        except ImportError:
            logger.info("Auto-classify skipped: numpy not available")
            return

        # Only use categories with ≥3 skills — smaller categories produce
        # sharp centroids that pull in unrelated skills (e.g. "subagent" with
        # 1 skill attracting imessage/ocr-and-documents).
        min_skills = 3
        viable = {cat: texts for cat, texts in cat_texts.items() if len(texts) >= min_skills}
        if len(viable) < len(cat_texts):
            omitted = set(cat_texts) - set(viable)
            logger.debug("Auto-classify: omitted small categories (n<{}): {}", min_skills, omitted)

        if not viable:
            logger.info("Auto-classify: no category has ≥{} skills for centroid building", min_skills)
            return

        cat_centroids: dict[str, np.ndarray] = {}
        for cat, texts in viable.items():
            embs = model.encode(texts, normalize_embeddings=True)
            cat_centroids[cat] = embs.mean(axis=0)

        if not cat_centroids:
            return

        # Single threshold for all categories.  No catch-all bucket —
        # skills that don't match any existing category stay uncategorized
        # so the warning banner prompts the LLM to create a meaningful label.
        threshold = 0.55

        modified = 0
        for s in uncategorized:
            desc = s.get("desc", "")
            if not desc:
                continue

            emb = model.encode([desc], normalize_embeddings=True)[0]
            scored = [(cat, float(np.dot(emb, centroid))) for cat, centroid in cat_centroids.items()]
            scored.sort(key=lambda x: -x[1])

            best_cat, best_score = scored[0]

            if best_score >= threshold:
                assign_cat = best_cat
            else:
                logger.debug("Auto-classify: '{}' best={}@{:.3f} < threshold={} — rejected",
                             s["name"], best_cat, best_score, threshold)
                continue

            if self._set_skill_category(s["file"], s["content"], assign_cat):
                modified += 1
                s["category"] = assign_cat
                logger.info("Auto-classified '{}' → {} (score={:.3f})", s["name"], assign_cat, best_score)

        if modified:
            logger.info("Auto-classified {} skills total", modified)

    @staticmethod
    def _set_skill_category(file_path: Path, content: str, category: str) -> bool:
        """Add or update ``category:`` in SKILL.md frontmatter.  Returns True if file changed."""
        m = _FRONTMATTER_RE.search(content)
        if not m:
            return False

        fm_text = m.group(1)
        cat_re = re.compile(r"^category:\s*(.*)$", re.MULTILINE)

        if cat_re.search(fm_text):
            new_fm = cat_re.sub(f"category: {category}", fm_text)
        else:
            new_fm = f"category: {category}\n" + fm_text

        if new_fm == fm_text:
            return False

        new_content = content[:m.start(1)] + new_fm + content[m.end(1):]
        file_path.write_text(new_content, encoding="utf-8")
        return True

    def refresh_skills_index(self) -> bool:
        """Rebuild skills FAISS index if any workspace SKILL.md changed since last build.

        Two-level check:
          1. Directory mtime — fast path that catches add/delete/modify (works on NTFS).
          2. Per-file mtime — catches in-place modifications on filesystems where
             directory mtime doesn't update when file content changes.

        Only checks workspace skills; built-in skills never change at runtime.
        Returns True if index was rebuilt.
        """
        if not self._last_skills_rebuild:
            return False
        ws = self.skills_loader.workspace_skills
        if not ws or not ws.exists():
            return False

        # Level 1: directory mtime (catches add/delete, fast — one stat call)
        try:
            dir_mtime = ws.stat().st_mtime
            changed = dir_mtime > self._last_skills_rebuild
        except OSError:
            changed = False  # can't stat dir, skip fast path

        # Level 2: per-file mtime (catches in-place modification)
        if not changed:
            try:
                for d in ws.iterdir():
                    skill_file = d / "SKILL.md"
                    if skill_file.exists():
                        if skill_file.stat().st_mtime > self._last_skills_rebuild:
                            changed = True
                            break
            except OSError:
                changed = True  # be safe — rebuild rather than miss changes

        if changed:
            logger.info("Workspace skills changed — rebuilding FAISS index")
            self.build_skills_index()
            return True
        return False

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
        content = entry.rstrip()
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
