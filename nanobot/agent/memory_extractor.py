"""MemoryExtractor — cron-scheduled memory extraction from saved prompts (.pt files).

Replaces the old Consolidator + Dream two-stage pipeline.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.agent.memory_store import MemoryStore


_SESSION_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]")
_SANITIZE_MAX_LEN = 64

_ANALYSIS_MAX_CHARS = 200_000  # Max chars of .pt content sent to analysis LLM


class MemoryExtractor:
    """Two-step memory processor: extract findings from .pt files, then write + cleanup.

    Step 1 — Extract: process saved prompts, call LLM to find new information.
    Step 2 — Write + Cleanup: write findings to files, cleanup-check SOUL.md/USER.md,
             git commit once, rebuild FAISS.
    """

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_tool_result_chars: int = 32_000,
        timezone: str | None = None,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.timezone = timezone
        self.prompts_dir = ensure_dir(store.workspace / "prompts")
        self.failed_dir = ensure_dir(self.prompts_dir / "failed")
        self.processed_dir = ensure_dir(self.prompts_dir / "processed")

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        """Step 0 → Step 1 → Step 2. Returns True if any work was done."""
        all_findings: list[dict[str, Any]] = []

        # ── Step 0: detect memory/ changes from external writes ──
        if self._memory_dir_changed():
            self._generate_memory_index()
            self._add_backlinks()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.store.build_vector_index)
            await loop.run_in_executor(None, self.store.build_framework_index)
            if self.store.git.is_initialized():
                self.store.git.auto_commit("memory: sync external changes")

        # ── Step 1: extract findings from .pt files ──
        pt_files = sorted(self.prompts_dir.glob("*.pt"))
        if not pt_files:
            logger.debug("MemoryExtractor: no .pt files to process")
            return False

        for pt_path in pt_files:
            processing_path = pt_path.with_suffix(".pt.processing")
            try:
                pt_path.rename(processing_path)
            except OSError:
                logger.warning("MemoryExtractor: race on {}, skipping", pt_path)
                continue

            try:
                content = json.loads(processing_path.read_text(encoding="utf-8"))
                analysis = await self._analysis_llm(content)
                if analysis:
                    findings = analysis.get("findings", [])
                    if findings:
                        all_findings.extend(findings)
                        logger.info(
                            "MemoryExtractor: {} findings from {}",
                            len(findings),
                            processing_path.name,
                        )
                processed_name = processing_path.name.replace(".pt.processing", ".pt")
                processing_path.rename(self.processed_dir / processed_name)
            except Exception:
                logger.exception("MemoryExtractor: failed to process {}", processing_path)
                self.failed_dir.mkdir(parents=True, exist_ok=True)
                failed_name = processing_path.name.replace(".pt.processing", ".pt")
                processing_path.rename(self.failed_dir / failed_name)

        if not all_findings:
            logger.info("MemoryExtractor: Step 1 done, no findings; skipping Step 2")
            return False

        # ── Step 2: write findings + cleanup ──
        await self._write_cleanup_and_rebuild(all_findings)
        return True

    # ------------------------------------------------------------------
    # Step 1 — LLM analysis
    # ------------------------------------------------------------------

    async def _analysis_llm(
        self, pt_content: dict
    ) -> dict[str, Any] | None:
        """Call LLM to analyze a saved prompt, return parsed JSON."""
        # Serialize for LLM consumption. Truncate from front if too large.
        pt_text = json.dumps(pt_content, ensure_ascii=False, indent=2)
        if len(pt_text) > _ANALYSIS_MAX_CHARS:
            pt_text = "... (conversation start truncated)\n" + pt_text[-_ANALYSIS_MAX_CHARS:]

        prompt = render_template("agent/extractor_analysis.md")

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": pt_text},
                ],
                tools=None,
                tool_choice=None,
            )
        except Exception:
            logger.exception("MemoryExtractor: analysis LLM call failed")
            return None

        raw = (response.content or "").strip()
        if not raw:
            return None

        return self._parse_json_output(raw)

    @staticmethod
    def _parse_json_output(raw: str) -> dict[str, Any] | None:
        """Parse and validate the LLM JSON response."""
        # Try to extract JSON from markdown code block if present
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("MemoryExtractor: failed to parse LLM JSON output")
            return None

        if not isinstance(result, dict) or "findings" not in result:
            return None

        findings = result.get("findings", [])
        if not isinstance(findings, list):
            result["findings"] = []
            return result

        valid = []
        for f in findings:
            if isinstance(f, dict) and "type" in f and "content" in f:
                valid.append(f)
        result["findings"] = valid
        return result

    # ------------------------------------------------------------------
    # Step 2 — Write findings + cleanup
    # ------------------------------------------------------------------

    async def _write_cleanup_and_rebuild(self, findings: list[dict[str, Any]]) -> None:
        """Write all findings to target files, then cleanup-check SOUL.md/USER.md, then commit and rebuild FAISS."""
        topic_files: dict[str, list[str]] = {}  # rel_path → [content lines]

        for finding in findings:
            ftype = finding.get("type", "skip")
            if ftype == "skip":
                continue

            content = (finding.get("content") or "").strip()
            if not content:
                continue

            if ftype == "preference":
                topic_files.setdefault("user.md", []).append(f"- {content}")

            elif ftype == "skill":
                name = (finding.get("name") or "").strip()
                if name and content:
                    topic_files.setdefault(
                        "pending_skills.md", []
                    ).append(f"- **{name}**: {content}")

            elif ftype in ("knowledge", "pitfall", "pattern"):
                topic = (finding.get("topic") or "").strip()
                if not topic:
                    continue
                rel_path = self._topic_to_filepath(topic) + ".md"

                if ftype == "pitfall":
                    paragraph = f"- ⚠️ {content}"
                elif ftype == "pattern":
                    paragraph = f"- 💡 {content}"
                else:
                    paragraph = f"- {content}"

                topic_files.setdefault(rel_path, []).append(paragraph)

        # ── Flush additions to files ──
        changed = bool(topic_files)

        if topic_files:
            for rel_path, paragraphs in topic_files.items():
                full_path = self.store.memory_dir / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if full_path.exists():
                    with open(full_path, "a", encoding="utf-8") as f:
                        for p in paragraphs:
                            f.write(f"\n\n{p}\n")
                else:
                    lines = [f"# {full_path.stem}\n"]
                    for p in paragraphs:
                        lines.append(f"\n{p}\n")
                    lines.append(f"\n---\n\n*创建: {date_str}*\n")
                    full_path.write_text("".join(lines), encoding="utf-8")
                logger.info(
                    "MemoryExtractor: wrote {} paragraph(s) to {}",
                    len(paragraphs),
                    rel_path,
                )

        if not changed:
            logger.info("MemoryExtractor: no actionable findings to write")
            return

        # ── Re-generate MEMORY.md index ──
        self._generate_memory_index()

        # ── Step 2b: cleanup check ──
        await self._cleanup_check()

        # ── Git commit ──
        if self.store.git.is_initialized():
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            msg = f"extractor: {utc_now} UTC, {len(findings)} finding(s)"
            sha = self.store.git.auto_commit(msg)
            if sha:
                logger.info("MemoryExtractor: committed {}", sha)

        # ── FAISS rebuild ──
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.store.build_vector_index)
        await loop.run_in_executor(None, self.store.build_framework_index)

    # ------------------------------------------------------------------
    # Skill creation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Memory directory change detection
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_memory_dir(memory_dir: Path) -> dict[str, int]:
        """Scan memory/ and return {relative_path: mtime_ns} for all .md files."""
        snapshot: dict[str, int] = {}
        exclude_names = {"MEMORY.md", "topic-map.json"}
        for p in sorted(memory_dir.rglob("*.md")):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            try:
                snapshot[str(p.relative_to(memory_dir))] = p.stat().st_mtime_ns
            except OSError:
                continue
        return snapshot

    def _memory_dir_changed(self) -> bool:
        """Check if memory/ directory contents have changed since last check."""
        state_file = self.store.memory_dir / ".memory_state.json"
        current = self._snapshot_memory_dir(self.store.memory_dir)

        if state_file.exists():
            try:
                previous = json.loads(state_file.read_text(encoding="utf-8"))
                if current == previous:
                    return False
            except (json.JSONDecodeError, OSError):
                pass

        state_file.write_text(
            json.dumps(current, ensure_ascii=False), encoding="utf-8"
        )
        return True

    # ------------------------------------------------------------------
    # Auto-linking (See also backlinks between memory files)
    # ------------------------------------------------------------------

    def _build_reference_index(self) -> dict[str, set[str]]:
        """Build {lowercase_term: set[rel_path]} from memory file headings.

        Extracts H1 titles and ## section headings from each memory file.
        Only keeps terms with >=2 words to reduce false positives.
        Returns mapping from normalized term to set of file paths.
        """
        index: dict[str, set[str]] = {}
        exclude_names = {"MEMORY.md", "topic-map.json"}

        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            try:
                rel = str(p.relative_to(self.store.memory_dir))
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue

            # H1 title → derive terms from filename stem
            stem = p.stem.lower().replace("_", " ").replace("-", " ")
            if len(stem.split()) >= 2:
                index.setdefault(stem, set()).add(rel)

            # H1 and ## headings
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    heading = line.lstrip("# ").strip().lower()
                    words = heading.split()
                    if len(words) >= 2 and len(heading) <= 80:
                        index.setdefault(heading, set()).add(rel)
                elif line.startswith("## "):
                    heading = line.lstrip("# ").strip().lower()
                    words = heading.split()
                    if len(words) >= 2 and len(heading) <= 80:
                        index.setdefault(heading, set()).add(rel)

        return index

    def _add_backlinks(self) -> None:
        """Add '## See also' sections to memory files based on cross-references.

        Scans each memory file's content for occurrences of other files' headings.
        Only matches terms >= 2 words long, using word-boundary regex.
        Only writes a file if its See also section actually changed.
        """
        ref_index = self._build_reference_index()
        if not ref_index:
            return

        exclude_names = {"MEMORY.md", "topic-map.json"}
        # Sort terms by length (longest first) to prefer multi-word matches
        sorted_terms = sorted(ref_index.keys(), key=len, reverse=True)

        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            try:
                rel = str(p.relative_to(self.store.memory_dir))
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue

            # Find which other files this file references
            referenced: set[str] = set()
            text_lower = text.lower()
            for term in sorted_terms:
                target_files = ref_index[term]
                # Build word-boundary pattern for multi-word term
                escaped = re.escape(term)
                if re.search(rf"(?<![a-zA-Z一-鿿]){escaped}(?![a-zA-Z一-鿿])", text_lower):
                    for tf in target_files:
                        if tf != rel:
                            referenced.add(tf)

            if not referenced:
                continue

            # Build See also section
            see_also_lines = ["\n## See also\n"]
            for ref in sorted(referenced):
                title = Path(ref).stem
                see_also_lines.append(f"- [{title}]({ref})\n")

            see_also_text = "".join(see_also_lines)

            # Replace existing See also section or append
            existing_see_also = re.search(r"\n## See also\n.*?(?=\n## |\Z)", text, re.DOTALL)
            if existing_see_also:
                new_text = text[:existing_see_also.start()] + see_also_text + text[existing_see_also.end():]
            else:
                new_text = text.rstrip() + see_also_text

            if new_text != text:
                p.write_text(new_text, encoding="utf-8")
                logger.debug("MemoryExtractor: added backlinks to {}", rel)

    # ------------------------------------------------------------------
    # MEMORY.md auto-generation
    # ------------------------------------------------------------------

    def _generate_memory_index(self) -> None:
        """Scan memory/ directory and generate MEMORY.md index."""
        exclude_names = {"MEMORY.md", "topic-map.json"}

        files = sorted(
            p.relative_to(self.store.memory_dir)
            for p in self.store.memory_dir.rglob("*.md")
            if ".vector_index" not in p.parts and p.name not in exclude_names
        )

        if not files:
            return

        lines = ["# Memory\n", ""]
        category_index: dict[str, list[Path]] = {}
        for rel in files:
            cat = rel.parent if rel.parent.name != "." else "."
            category_index.setdefault(cat, []).append(rel)

        for cat in sorted(category_index, key=lambda c: (c == ".", c if isinstance(c, str) else str(c))):
            cat_files = category_index[cat]
            label = str(cat) if cat != "." else "misc"
            lines.append(f"## {label}\n")
            for rel in cat_files:
                link = rel.as_posix()
                lines.append(f"- [{rel.stem}]({link})")
            lines.append("")

        self.store.memory_file.write_text(
            "\n".join(lines), encoding="utf-8"
        )
        logger.info(
            "MemoryExtractor: re-generated MEMORY.md with {} file(s)", len(files)
        )

    # ------------------------------------------------------------------
    # Cleanup check
    # ------------------------------------------------------------------

    async def _cleanup_check(self) -> None:
        """Step 2b: LLM check SOUL.md/USER.md for contradictions, duplicates, stale content."""
        soul = self.store.read_soul()
        user = self.store.read_user()
        if not soul and not user:
            return

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template("agent/extractor_cleanup.md"),
                    },
                    {
                        "role": "user",
                        "content": f"## SOUL.md\n{soul or '(empty)'}\n\n## USER.md\n{user or '(empty)'}",
                    },
                ],
                tools=None,
                tool_choice=None,
            )
        except Exception:
            logger.exception("MemoryExtractor: cleanup LLM call failed")
            return

        raw = (response.content or "").strip()
        if not raw:
            return

        parsed = self._parse_json_output(raw)
        if not parsed:
            return

        suggestions = parsed.get("suggestions", [])
        if not suggestions:
            return

        for s in suggestions:
            action = s.get("action", "keep")
            if action == "keep":
                continue
            file_name = s.get("file", "")
            target = s.get("target_text", "")
            replacement = s.get("replacement")

            if file_name == "SOUL.md":
                file_path = self.store.soul_file
            elif file_name == "USER.md":
                file_path = self.store.user_file
            else:
                continue

            try:
                current = file_path.read_text(encoding="utf-8")
                if target and target in current:
                    if action == "remove":
                        new_content = current.replace(target, "", 1)
                    elif action == "rewrite" and replacement:
                        new_content = current.replace(target, replacement, 1)
                    else:
                        continue
                    file_path.write_text(new_content, encoding="utf-8")
                    logger.info(
                        "MemoryExtractor: {} in {}: {}",
                        action,
                        file_name,
                        s.get("reason", ""),
                    )
            except OSError:
                logger.warning(
                    "MemoryExtractor: failed to apply cleanup to {}", file_name
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a single path component (no slashes)."""
        safe = _SESSION_KEY_RE.sub("_", name)
        return safe[: _SANITIZE_MAX_LEN].strip("_")

    @staticmethod
    def _topic_to_filepath(topic: str) -> str:
        """Convert a hierarchical topic (e.g. 'AI/harness design') into a safe relative path.

        Preserves forward slashes as directory separators so the LLM can
        organize knowledge into nested directories like AI/harness-design.md.
        """
        parts = topic.strip("/").split("/")
        safe_parts = [MemoryExtractor._sanitize_filename(p) for p in parts]
        safe_parts = [p for p in safe_parts if p]  # remove empties
        return "/".join(safe_parts[:8])  # max 8 levels deep

    @staticmethod
    def save_prompt_snapshot(
        messages: list[dict[str, Any]], prompts_dir: Path, session_key: str
    ) -> Path:
        """Save a .pt snapshot of the messages array before LLM send.

        Called from the message pipeline (not from MemoryExtractor itself).
        Returns the path to the saved file.
        """
        safe_key = MemoryExtractor._sanitize_filename(session_key)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"{safe_key}-{ts}.pt"
        path = prompts_dir / filename

        payload = {
            "session_key": session_key,
            "saved_at": ts,
            "messages": messages,
        }

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug("Saved .pt: {}", filename)
        return path
