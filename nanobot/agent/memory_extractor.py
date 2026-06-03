"""MemoryExtractor — cron-scheduled memory extraction from saved prompts (.pt files).

Replaces the old Consolidator + Dream two-stage pipeline.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
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


def _trim_sentence(text: str, max_len: int = 150) -> str:
    """Trim text to max_len, cutting at sentence boundary when possible."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    for sep in ("。", "！", "？", ". ", "! ", "? "):
        idx = truncated.rfind(sep)
        if idx > max_len * 0.4:
            return truncated[:idx + len(sep)].strip()
    return truncated.rstrip() + "…"


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
        did_work = False

        # ── Step 0: detect memory/ changes from external writes ──
        if self._memory_dir_changed():
            self._add_backlinks()
            await self._rebuild_indexes()
            if self.store.git.is_initialized():
                self.store.git.auto_commit("memory: sync external changes")

        # ── Consolidate fragmented small topic files (independent of .pt files) ──
        if await self._consolidate_memory():
            did_work = True

        # ── Step 1: extract findings from .pt files ──
        pt_files = sorted(self.prompts_dir.glob("*.pt"))
        if not pt_files:
            logger.debug("MemoryExtractor: no .pt files to process")
            if did_work:
                self._add_backlinks()
                await self._rebuild_indexes()
                if self.store.git.is_initialized():
                    self.store.git.auto_commit("memory: consolidate fragmented files")
            return did_work

        session_summaries: list[str] = []

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
                    summary = (analysis.get("session_summary") or "").strip()
                    if summary:
                        session_summaries.append(summary)
                processed_name = processing_path.name.replace(".pt.processing", ".pt")
                processing_path.replace(self.processed_dir / processed_name)
            except Exception:
                logger.exception("MemoryExtractor: failed to process {}", processing_path)
                self.failed_dir.mkdir(parents=True, exist_ok=True)
                failed_name = processing_path.name.replace(".pt.processing", ".pt")
                processing_path.rename(self.failed_dir / failed_name)

        if not all_findings and not session_summaries:
            logger.info("MemoryExtractor: Step 1 done, no findings; skipping Step 2")
            if did_work:
                self._add_backlinks()
                await self._rebuild_indexes()
                if self.store.git.is_initialized():
                    self.store.git.auto_commit("memory: consolidate fragmented files")
            return did_work

        # ── Step 2: write findings + cleanup ──
        await self._write_cleanup_and_rebuild(all_findings, session_summaries)
        return True

    # ------------------------------------------------------------------
    #  Recent findings tracking for MEMORY.md Recent changes
    # ------------------------------------------------------------------

    _RECENT_JSON = ".recent.json"  # inside memory_dir

    def _load_recent_findings(self) -> list[dict[str, Any]]:
        """Load persisted recent findings (max 15)."""
        p = self.store.memory_dir / self._RECENT_JSON
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _append_recent_findings(self, new_entries: list[dict[str, Any]]) -> None:
        """Append findings and persist top 15."""
        existing = self._load_recent_findings()
        existing.extend(new_entries)
        existing.sort(key=lambda x: -x.get("ts", 0))
        # Dedup by (path, text) keeping first occurrence (newest due to sort)
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for e in existing:
            key = (e.get("path", ""), e.get("text", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(e)
        self.store.memory_dir.joinpath(self._RECENT_JSON).write_text(
            json.dumps(deduped[:12], ensure_ascii=False), encoding="utf-8",
        )

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
    def _parse_json_output(raw: str, required_key: str = "findings") -> dict[str, Any] | None:
        """Parse and validate the LLM JSON response."""
        clean = MemoryExtractor._extract_json_from_llm_output(raw)

        try:
            result = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("MemoryExtractor: failed to parse LLM JSON output")
            return None

        if not isinstance(result, dict) or required_key not in result:
            return None

        if required_key != "findings":
            return result

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

    async def _write_cleanup_and_rebuild(self, findings: list[dict[str, Any]], session_summaries: list[str] | None = None) -> None:
        """Write all findings to target files, then cleanup-check SOUL.md/USER.md, then commit and rebuild FAISS."""
        topic_files: dict[str, list[str]] = {}  # rel_path → [content lines]
        pinned_paragraphs: set[str] = set()  # paragraphs marked as pinned
        recent_candidates: list[dict[str, Any]] = []  # (rel_path, content, heading) for Recent section
        modified_for_cleanup: set[str] = set()  # rel_paths written to

        for finding in findings:
            ftype = finding.get("type", "skip")
            if ftype == "skip":
                continue

            content = (finding.get("content") or "").strip()
            if not content:
                continue
            # Quality gate: reject vague Chinese advice without technical substance
            if re.match(r"^[-*—\s]*(注意|建议|需要|应该|可以|最好|不要)[：:]\s*[^，。]*[的了]$", content):
                logger.debug("MemoryExtractor: skipped vague finding: {}", content[:60])
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

                paragraph = self._format_finding_paragraph(ftype, content)

                supersedes = (finding.get("supersedes") or "").strip()
                if supersedes:
                    # Embed pinned marker directly in replacement text
                    replacement = paragraph
                    if finding.get("pinned") and "<!--pinned-->" not in replacement:
                        replacement = replacement.rstrip() + "\n<!--pinned-->"
                    applied, modified_path = self._apply_supersedes(supersedes, replacement)
                    if applied:
                        track_path = modified_path or rel_path
                        logger.info(
                            "MemoryExtractor: supersedes applied in {} for '{}'",
                            track_path, content[:60],
                        )
                        modified_for_cleanup.add(track_path)
                        continue
                    logger.info(
                        "MemoryExtractor: supersedes target not found for '{}', falling back to append",
                        supersedes[:60],
                    )

                topic_files.setdefault(rel_path, []).append(paragraph)

                if finding.get("pinned"):
                    pinned_paragraphs.add(paragraph)

            else:
                logger.warning("MemoryExtractor: unknown finding type '{}', dropped", ftype)

        # ── Flush additions to files ──
        changed = bool(topic_files or modified_for_cleanup)

        if topic_files:
            for rel_path, paragraphs in topic_files.items():
                full_path = self.store.memory_dir / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if full_path.exists():
                    existing = full_path.read_text(encoding="utf-8")
                    new_paragraphs = [p for p in paragraphs if p.strip() not in existing]
                    skipped = len(paragraphs) - len(new_paragraphs)
                    if new_paragraphs:
                        with open(full_path, "a", encoding="utf-8") as f:
                            for p in new_paragraphs:
                                f.write(f"\n\n{p}\n")
                                if p in pinned_paragraphs:
                                    f.write("<!--pinned-->\n")
                        modified_for_cleanup.add(rel_path)
                    if skipped:
                        logger.info(
                            "MemoryExtractor: skipped {} duplicate(s) in {}",
                            skipped, rel_path,
                        )
                else:
                    lines = [f"# {full_path.stem}\n"]
                    for p in paragraphs:
                        lines.append(f"\n{p}\n")
                        if p in pinned_paragraphs:
                            lines.append("<!--pinned-->\n")
                    lines.append(f"\n---\n\n*创建: {date_str}*\n")
                    full_path.write_text("".join(lines), encoding="utf-8")
                    modified_for_cleanup.add(rel_path)
                logger.info(
                    "MemoryExtractor: wrote {} paragraph(s) to {}",
                    len(paragraphs),
                    rel_path,
                )

        # ── Append session summaries to recent changes ──
        if session_summaries:
            ts = time.time()
            for summary in session_summaries:
                recent_candidates.append({
                    "path": "_session_work.md",
                    "text": summary,
                    "ts": ts,
                })

        # ── Capture written findings for Recent changes ──
        if recent_candidates:
            self._append_recent_findings(recent_candidates)

        if not changed:
            logger.info("MemoryExtractor: no actionable findings to write")
            return

        # ── Step 2b: materialize skills from pending_skills.md ──
        if "pending_skills.md" in topic_files:
            await self._materialize_skills()

        # ── Git commit (findings + skills, before cleanup/backlinks for safety) ──
        if self.store.git.is_initialized():
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            msg = f"extractor: {utc_now} UTC, {len(findings)} finding(s)"
            sha = self.store.git.auto_commit(msg)
            if sha:
                logger.info("MemoryExtractor: committed {}", sha)

        # ── Step 2c: cleanup check (after commit, safe to modify files) ──
        await self._cleanup_check(list(modified_for_cleanup))

        # ── Backlinks (after commit, safe to modify) ──
        self._add_backlinks()

        # ── FAISS rebuild ──
        await self._rebuild_indexes()

        # ── Commit post-processing (cleanup + backlinks) if any changes ──
        if self.store.git.is_initialized():
            sha = self.store.git.auto_commit("memory: cleanup and backlinks")
            if sha:
                logger.info("MemoryExtractor: committed post-processing {}", sha)

    # ------------------------------------------------------------------
    # Supersedes — replace old content with new content using FAISS
    # ------------------------------------------------------------------

    @staticmethod
    def _format_finding_paragraph(ftype: str, content: str) -> str:
        """Format a finding as a markdown paragraph."""
        if ftype == "pitfall":
            return f"- ⚠️ {content}"
        elif ftype == "pattern":
            return f"- 💡 {content}"
        return f"- {content}"

    def _apply_supersedes(self, supersedes: str, new_paragraph: str) -> tuple[bool, str | None]:
        """Search memory files for *supersedes* text and replace with *new_paragraph*.

        Uses FAISS to find the best-matching file and chunk, then does paragraph-level
        matching within that file for precision.

        Returns ``(True, modified_rel_path)`` if replacement was done,
        ``(False, None)`` if no suitable target was found.
        """
        # Search FAISS for the superseded content
        results = self.store.vector_index.search(supersedes, k=3, min_score=0.3)

        if not results:
            # Fallback: grep across all memory files
            results = self._full_text_search(supersedes)

        if not results:
            return (False, None)

        for result in results:
            source = result.get("source", "")
            if not source:
                continue
            full_path = self.store.memory_dir / source
            if not full_path.exists():
                continue
            if self._replace_in_file(full_path, supersedes, new_paragraph):
                return (True, source)

        return (False, None)

    @staticmethod
    def _replace_in_file(path: Path, old_text: str, new_text: str) -> bool:
        """Replace *old_text* with *new_text* at paragraph level in *path*.

        Splits the file into blank-line-separated paragraphs and uses
        ``difflib.SequenceMatcher`` to find the best match for *old_text*.
        Returns True if a match was found and replaced.
        """
        text = path.read_text(encoding="utf-8").strip()
        raw_paragraphs = re.split(r"\n\n+", text)
        # Filter out empty paragraphs that arise from leading/trailing whitespace
        paragraphs = [p for p in raw_paragraphs if p.strip()]

        best_idx = -1
        best_ratio = 0.0
        old_lower = old_text.lower()

        for i, para in enumerate(paragraphs):
            ratio = difflib.SequenceMatcher(None, old_lower, para.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i

        if best_ratio > 0.65 and best_idx >= 0:
            paragraphs[best_idx] = new_text
            path.write_text("\n\n".join(paragraphs), encoding="utf-8")
            return True

        return False

    def _full_text_search(self, query: str) -> list[dict[str, Any]]:
        """Fallback: simple substring search across all memory files."""
        results: list[dict[str, Any]] = []
        q = query.lower()
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in ("MEMORY.md", "index.md"):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if q in content.lower():
                rel = p.relative_to(self.store.memory_dir).as_posix()
                results.append({"source": rel, "text": content[:200], "score": 0.5})
        return results

    # ------------------------------------------------------------------
    # Skill creation — Phase 2: pending_skills.md → skills/<name>/SKILL.md
    # ------------------------------------------------------------------

    async def _materialize_skills(self) -> None:
        """Convert pending_skills.md entries to real skills/<name>/SKILL.md files."""
        pending_path = self.store.memory_dir / "pending_skills.md"
        if not pending_path.exists():
            return

        pending_text = pending_path.read_text(encoding="utf-8").strip()
        if not pending_text:
            return

        # Scan existing skills for dedup
        skills_dir = self.store.workspace / "skills"
        existing_skills: list[dict[str, str]] = []
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                if not child.is_dir():
                    continue
                skill_file = child / "SKILL.md"
                if skill_file.exists():
                    content = skill_file.read_text(encoding="utf-8")
                    name = ""
                    desc = ""
                    for line in content.split("\n"):
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                    existing_skills.append({"name": name, "description": desc, "path": child.name})

        existing_text = "\n".join(
            f"- {s['name']}: {s['description']}" for s in existing_skills
        ) if existing_skills else "(none)"

        user_content = (
            f"## Pending skill entries\n\n{pending_text}\n\n"
            f"## Existing skills\n\n{existing_text}"
        )

        prompt = render_template("agent/extractor_skill_creator.md")
        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=16384,
                tools=None,
                tool_choice=None,
            )
        except Exception:
            logger.exception("MemoryExtractor: skill creation LLM call failed")
            return

        raw = (response.content or "").strip()
        if not raw:
            return

        try:
            raw_clean = self._extract_json_from_llm_output(raw)
            parsed = json.loads(raw_clean)
        except json.JSONDecodeError:
            logger.warning("MemoryExtractor: failed to parse skill creator JSON output (raw[:300]={}..., cleaned={})", raw[:300], raw_clean[:800])
            return

        skills = parsed.get("skills", []) if isinstance(parsed, dict) else []
        if not skills:
            logger.info("MemoryExtractor: no skills to create (all deduped or skipped)")
            return

        created: list[str] = []
        for skill in skills:
            name = skill.get("name", "").strip()
            content = skill.get("content", "").strip()
            if not name or not content:
                continue

            skill_dir = skills_dir / name
            if skill_dir.exists():
                logger.info("MemoryExtractor: skill dir already exists, skipping: {}", name)
                continue

            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
            created.append(name)
            logger.info("MemoryExtractor: created skill: {}", name)

        if not created:
            return

        # Remove materialized entries from pending_skills.md
        self._remove_materialized_from_pending(pending_path, created)

        # Git commit for new skills
        if self.store.git.is_initialized():
            sha = self.store.git.auto_commit(
                f"extractor: created {len(created)} skill(s): {', '.join(created)}"
            )
            if sha:
                logger.info("MemoryExtractor: committed skill creation {}", sha)

    @staticmethod
    def _extract_json_from_llm_output(text: str) -> str:
        """Extract JSON from LLM output that may contain <think> tags and markdown fences."""
        # Step 1: isolate content after </think> (the actual JSON output)
        think_end = text.find("</think>")
        if think_end >= 0:
            after_think = text[think_end + len("</think>"):].strip()
        else:
            # <think> unclosed or absent — try the whole text
            after_think = text.strip()
            # strip leading <think> if present, keep rest for JSON search
            after_think = after_think.removeprefix("<think>").strip()
            if not after_think:
                # <think> with nothing after — no JSON possible
                return ""
        # Step 2: find the LAST ```json or ``` code block
        matches = list(re.finditer(r"```(?:json)?\s*\n(.*?)\n```", after_think, re.DOTALL))
        if matches:
            return matches[-1].group(1).strip()
        # Step 3: try to find standalone { ... } JSON object
        brace_start = after_think.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(after_think)):
                if after_think[i] == "{":
                    depth += 1
                elif after_think[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return after_think[brace_start : i + 1]
        # Step 4: return as-is and let json.loads fail if invalid
        return after_think

    @staticmethod
    def _remove_materialized_from_pending(pending_path: Path, created_names: list[str]) -> None:
        """Remove materialized skill entries from pending_skills.md."""
        text = pending_path.read_text(encoding="utf-8")
        lines = text.split("\n")
        kept: list[str] = []
        removed = 0
        for line in lines:
            if any(f"**{name}**" in line for name in created_names):
                removed += 1
                continue
            kept.append(line)

        if removed:
            pending_path.write_text("\n".join(kept), encoding="utf-8")
            logger.info(
                "MemoryExtractor: removed {} materialized entry(ies) from pending_skills.md",
                removed,
            )

    # ------------------------------------------------------------------
    # Memory consolidation — merge narrow topic files
    # ------------------------------------------------------------------

    async def _rebuild_indexes(self) -> None:
        """Regenerate index.md, MEMORY.md, tree.json, and rebuild FAISS indexes."""
        self._generate_index_files()
        self._generate_memory_index()
        self._generate_tree_json()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.store.build_vector_index)
        await loop.run_in_executor(None, self.store.build_framework_index)

    async def _consolidate_memory(self) -> bool:
        """Consolidate memory files: merge small files, reorganize dirs under 20 limit.

        Supports three operations:
        - ``merge_files``: combine small related files within one directory
        - ``merge_dirs``: merge a whole directory into another (files moved, source removed)
        - ``move_file``: move a single file to a different directory (topic rename)

        Returns True if any changes were executed.
        """
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md", "pending_skills.md", "lessons.md", "self_mod.md", "system.md", "user.md"}

        # Collect ALL topic directories and their files
        all_topics: dict[str, list[str]] = {}  # dir → [filenames]
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir)
            parent = str(rel.parent)
            all_topics.setdefault(parent, []).append(rel.name)

        # Count topic directories (exclude root ".")
        topic_dirs = sorted(d for d in all_topics if d != ".")
        total_dirs = len(topic_dirs)
        over_limit = total_dirs >= 20

        # Collect small files (≤10 lines, ≥3 per dir) for merge candidates
        small_candidates: dict[str, list[tuple[str, int]]] = {}
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir)
            if rel.parent.name == ".":
                continue
            parent = str(rel.parent)
            text = p.read_text(encoding="utf-8")
            lines = len(text.splitlines())
            if lines <= 10:
                small_candidates.setdefault(parent, []).append((rel.name, lines))

        has_small_clusters = any(len(v) >= 3 for v in small_candidates.values())

        if not over_limit and not has_small_clusters:
            return False

        # ── Build prompt ──
        parts = [f"你正在整理知识库的目录结构。唯一约束：目录数不超过 20。当前 {total_dirs}/20。\n"]
        if over_limit:
            parts.append("⚠️ 超过上限，需要合并！")

        # Extract first heading from each file for content-aware organization
        def _first_heading(p: Path) -> str:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("## ") or stripped.startswith("# "):
                        return stripped
                return ""
            except OSError:
                return ""

        parts.append("\n### 当前目录")
        for d in topic_dirs:
            files = all_topics.get(d, [])
            parts.append(f"- {d}/ ({len(files)} 个文件)")
            for name in sorted(files):
                fpath = self.store.memory_dir / d / name
                heading = _first_heading(fpath)
                tag = f" ← {heading}" if heading else ""
                parts.append(f"  - {name}{tag}")

        if has_small_clusters:
            parts.append("\n### 小文件（候选合并）")
            for cat_dir, files in sorted(small_candidates.items()):
                if len(files) >= 3:
                    parts.append(f"\n{cat_dir}/")
                    for name, lines in sorted(files):
                        parts.append(f"  - {name} ({lines} 行)")

        user_content = "\n".join(parts)

        system_msg = (
            "你负责整理知识库目录。目标：让分类更清晰、容易查找。\n"
            "看文件的实际内容，把相关主题归类到一起。例如同一项目的散落笔记可以合并，"
            "同类型工具可以统一目录。不相关的不要硬凑。\n\n"
            "唯一硬约束：目录数不超过 20。如果当前已经满足，不做无意义的调整。\n\n"
            "可用操作（三种，按实际情况选用）：\n"
            "1. merge_dirs: 整个目录合并到另一个已存在的目录\n"
            '   {"type": "merge_dirs", "category": "", "sources": ["DirA"], "target": "DirB"}\n'
            "2. merge_files: 同一目录下合并多个小文件为一个\n"
            '   {"type": "merge_files", "category": "Path", "sources": ["a.md", "b.md"], "target": "ab.md"}\n'
            "3. move_file: 移动单个文件到合适目录\n"
            '   {"type": "move_file", "sources": ["f.md"], "category": "TargetDir", "target": "f.md"}\n\n'
            "规则：merge_dirs 的 target 目录必须已存在。没把握就不动。\n\n"
            '只输出 JSON：{"operations": [...]}'
        )

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_content},
                ],
                tools=None,
                tool_choice=None,
            )
        except Exception:
            logger.exception("MemoryExtractor: consolidation LLM call failed")
            return False

        raw = (response.content or "").strip()
        if not raw:
            return False

        try:
            raw_clean = self._extract_json_from_llm_output(raw)
            parsed = json.loads(raw_clean)
        except json.JSONDecodeError:
            logger.warning("MemoryExtractor: failed to parse consolidation JSON (raw[:500]={})", raw[:500])
            return False

        operations = parsed.get("operations", []) if isinstance(parsed, dict) else []
        if not operations:
            logger.info("MemoryExtractor: no consolidation operations suggested")
            return False

        executed = False

        for op in operations:
            op_type = op.get("type", "")
            category = op.get("category", "")
            sources = op.get("sources", [])
            target = op.get("target", "")
            reason = op.get("reason", "")

            if op_type == "merge_files":
                if not target or not category or not sources:
                    continue
                cat_dir = self.store.memory_dir / category
                target_path = cat_dir / target
                if not cat_dir.is_dir() or target_path.exists():
                    continue
                combined: list[str] = [f"# {Path(target).stem}\n"]
                for src_name in sources:
                    src_path = cat_dir / src_name
                    if src_path.exists():
                        content = src_path.read_text(encoding="utf-8")
                        body_lines = content.split("\n")
                        body = "\n".join(
                            l for l in body_lines
                            if not l.startswith("# ") and not l.startswith("---")
                        )
                        combined.append(body.strip())
                        combined.append("")
                text = "\n".join(combined).strip()
                if not text:
                    continue
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                text += f"\n\n---\n\n*合并 Consolidation: {date_str}*"
                target_path.write_text(text, encoding="utf-8")
                for src_name in sources:
                    (cat_dir / src_name).unlink(missing_ok=True)
                logger.info("MemoryExtractor: merged {} files into {}/{}", len(sources), category, target)
                executed = True

            elif op_type == "merge_dirs":
                if not target or not sources:
                    continue
                target_dir = self.store.memory_dir / category / target if category else self.store.memory_dir / target
                if not target_dir.is_dir():
                    continue
                for src_name in sources:
                    src_dir = (self.store.memory_dir / category / src_name) if category else (self.store.memory_dir / src_name)
                    self._move_all_files(src_dir, target_dir)
                    # Remove empty source dir
                    try:
                        remaining = list(src_dir.rglob("*"))
                        if not remaining or all(
                            p.name == "index.md" or ".vector_index" in p.parts
                            for p in remaining
                        ):
                            import shutil
                            shutil.rmtree(src_dir)
                    except OSError:
                        pass
                    logger.info("MemoryExtractor: merged dir {} into {}", src_name, target)
                executed = True

            elif op_type == "move_file":
                if not target or not sources:
                    continue
                for src_path_str in sources:
                    src_parts = src_path_str.split("/", 1)
                    src_rel = src_path_str
                    full_src = self.store.memory_dir / src_rel
                    if not full_src.exists():
                        continue
                    # destination: category + target filename
                    dst_dir = self.store.memory_dir / category if category else self.store.memory_dir
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    dst_path = dst_dir / target
                    if dst_path.exists():
                        continue
                    full_src.rename(dst_path)
                    logger.info("MemoryExtractor: moved {} → {}/{}", src_rel, category, target)
                    executed = True

            else:
                logger.debug("MemoryExtractor: unknown consolidation op '{}', skipped", op_type)

        return executed

    @staticmethod
    def _move_all_files(src: Path, dst: Path) -> None:
        """Move all .md files from src into dst, preserving directory structure."""
        for p in src.rglob("*.md"):
            if ".vector_index" in p.parts or p.name == "index.md":
                continue
            rel = p.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.rename(target)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Memory directory change detection
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_memory_dir(memory_dir: Path) -> dict[str, int]:
        """Scan memory/ and return {relative_path: mtime_ns} for all .md files."""
        snapshot: dict[str, int] = {}
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md"}
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
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md"}

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

        exclude_names = {"MEMORY.md", "topic-map.json", "index.md"}
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
    # MEMORY.md + tree.json generation
    # ------------------------------------------------------------------

    def _generate_memory_index(self) -> None:
        """Scan memory/ and generate compact MEMORY.md for agent system prompt.

        Format: Recent changes (15 newest) + per-category summary with file count and topics.
        """
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md"}

        # Collect file metadata + pinned items
        file_meta: list[tuple[str, int, str, str, str]] = []  # (rel, mtime_ns, category, stem, heading)
        pinned_candidates: list[tuple[str, int, str]] = []  # (rel, mtime_ns, summary)
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir).as_posix()
            try:
                text = p.read_text(encoding="utf-8")
                mtime = p.stat().st_mtime_ns
            except OSError:
                continue
            if not text.strip():
                continue

            heading = ""
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("# "):
                    heading = s.lstrip("# ").strip()
                    break
            if not heading:
                heading = text.strip().split("\n")[0].strip()[:60]

            parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
            stem = Path(rel).stem
            file_meta.append((rel, mtime, parent, stem, heading))

            # Detect pinned — collect all, sort later
            if "<!--pinned-->" in text:
                summary = heading
                for line in text.split("\n"):
                    s = line.strip()
                    if s and not s.startswith("#") and "<!--pinned-->" not in s:
                        # Use description field if available, else first content line
                        if s.startswith("description:"):
                            summary = _trim_sentence(s[len("description:"):].strip())
                        else:
                            summary = _trim_sentence(s[:200].lstrip("- *💡⚠️ ").strip())
                        break
                pinned_candidates.append((rel, mtime, summary))

        # Sort pinned by recency (newest first), take top 6
        pinned_candidates.sort(key=lambda x: -x[1])
        rel_to_heading = {rel: h for rel, _, _, _, h in file_meta}
        pinned: list[str] = [
            f"- [{rel_to_heading.get(rel, rel)}]({rel}) — {summary}"
            for rel, _mtime, summary in pinned_candidates[:6]
        ]

        if not file_meta:
            return

        lines = ["# Memory\n", ""]

        if pinned:
            lines.append("## Pinned\n")
            lines.extend(pinned)
            lines.append("")

        # Per-category index
        category_index: dict[str, list[tuple[str, str, str]]] = {}
        for rel, _mtime, parent, stem, heading in file_meta:
            category_index.setdefault(parent, []).append((rel, stem, heading))

        # Recent changes — shows actual findings from last extraction(s)
        recent = self._load_recent_findings()
        if recent:
            now_s = time.time()
            two_days = 2 * 86400
            lines.append("## Recent changes\n")
            for r in recent:
                if r.get("path") != "_session_work.md":
                    continue  # only session summaries belong here
                text = r.get("text", "")
                ts = r.get("ts", 0)
                age_s = now_s - ts
                if age_s < two_days and text:
                    lines.append(f"- **{_trim_sentence(text, 200)}**")
                elif text:
                    lines.append(f"- {_trim_sentence(text, 200)}")
            lines.append("")

        # Category summary with clickable links (max 20 folders)
        cat_order = sorted(category_index, key=lambda c: (c == ".", c))[:20]
        for cat in cat_order:
            files = category_index[cat]
            label = cat if cat != "." else "misc"
            links: list[str] = []
            # Sub-directory links first (any depth)
            cat_path = self.store.memory_dir / cat if cat != "." else self.store.memory_dir
            if cat_path.is_dir():
                for child in sorted(cat_path.iterdir()):
                    if child.is_dir() and child.name != ".vector_index":
                        if any(f.is_file() and f.suffix == ".md" for f in child.rglob("*.md")):
                            sub_rel = f"{cat}/{child.name}/index.md" if cat != "." else f"{child.name}/index.md"
                            links.append(f"[{child.name}/]({sub_rel})")
            # File links after directories
            for rel, _stem, heading in sorted(files, key=lambda x: x[2]):
                display = heading if heading else Path(rel).stem
                links.append(f"[{display}]({rel})")
            topic_str = " · ".join(links[:8])
            if len(links) > 8:
                topic_str += " …"
            lines.append(f"- **{label}/** ({len(files)}) — {topic_str}")

        # Make category labels clickable (point to index.md), misc stays plain
        final: list[str] = []
        for line in lines:
            m = re.match(r"^- \*\*(.+?)\*\* \((\d+)\) — (.+)$", line)
            if m and m.group(1).rstrip("/") not in (".", "misc"):
                cat_name = m.group(1).rstrip("/")
                rest = f"{m.group(2)} — {m.group(3)}"
                final.append(f"- **[`{cat_name}/`]({cat_name}/index.md)** {rest}")
            else:
                final.append(line)
        lines[:] = final

        self.store.memory_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info(
            "MemoryExtractor: re-generated MEMORY.md with {} files in {} categories",
            len(file_meta), len(category_index),
        )

    def _generate_tree_json(self) -> None:
        """Generate tree.json for WebUI — file tree + recent changes."""
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md", "tree.json"}
        tree_path = self.store.memory_dir / "tree.json"

        tree: dict[str, Any] = {"recent": [], "tree": {}}
        recent_entries: list[dict[str, Any]] = []

        for p in sorted(self.store.memory_dir.rglob("*.md")):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir)
            try:
                text = p.read_text(encoding="utf-8")
                mtime = p.stat().st_mtime_ns
            except OSError:
                continue
            if not text.strip():
                continue

            # Extract H1 title and preview
            title = Path(rel).stem
            preview = ""
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("# "):
                    title = s.lstrip("# ").strip()
                elif s and not s.startswith("#") and not preview:
                    preview = s[:120]
                    break

            # Insert into nested tree dict
            parts = list(rel.parts[:-1])
            filename = rel.name
            current: dict = tree["tree"]
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[filename] = {"title": title, "mtime": mtime}

            recent_entries.append({
                "path": rel.as_posix(),
                "title": title,
                "mtime": mtime,
                "preview": preview,
            })

        recent_entries.sort(key=lambda x: -x["mtime"])
        tree["recent"] = recent_entries[:50]

        tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "MemoryExtractor: re-generated tree.json with {} entries", len(recent_entries),
        )

    # ------------------------------------------------------------------
    # Directory index generation
    # ------------------------------------------------------------------

    def _generate_index_files(self) -> None:
        """Generate index.md per directory for hierarchical navigation."""
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md", "pending_skills.md"}
        generated = 0

        # Collect all dirs that contain .md files
        dirs_with_md: set[Path] = set()
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            dirs_with_md.add(p.parent)

        for dir_path in sorted(dirs_with_md):
            rel = dir_path.relative_to(self.store.memory_dir).as_posix()
            files: list[tuple[str, str]] = []
            subdirs: list[str] = []

            for p in sorted(dir_path.iterdir()):
                if p.is_file() and p.suffix == ".md" and p.name not in exclude_names:
                    heading = p.stem
                    try:
                        for line in p.read_text(encoding="utf-8").split("\n"):
                            s = line.strip()
                            if s.startswith("# "):
                                heading = s.lstrip("# ").strip()
                                break
                    except OSError:
                        pass
                    files.append((p.name, heading))

                elif p.is_dir() and p.name != ".vector_index":
                    if any(
                        f.is_file() and f.suffix == ".md" and f.name not in exclude_names
                        for f in p.rglob("*.md")
                    ):
                        subdirs.append(p.name)

            if not files and not subdirs:
                continue

            label = str(rel) if str(rel) != "." else "Memory"
            lines = [f"# {label}\n", ""]

            if files:
                lines.append("## Files\n")
                for name, heading in files:
                    lines.append(f"- [{heading}]({name})")
                lines.append("")

            if subdirs:
                lines.append("## Subdirectories\n")
                for name in subdirs:
                    lines.append(f"- [{name}]({name}/index.md)")
                lines.append("")

            index_path = dir_path / "index.md"
            index_path.write_text("\n".join(lines), encoding="utf-8")
            generated += 1

        if generated:
            logger.info("MemoryExtractor: generated {} index.md file(s)", generated)

    # ------------------------------------------------------------------
    # Cleanup check
    # ------------------------------------------------------------------

    async def _cleanup_check(self, modified_files: list[str] | None = None) -> None:
        """Step 2b: LLM check SOUL.md/USER.md (and optionally modified topic files)
        for contradictions, duplicates, stale content."""
        soul = self.store.read_soul()
        user = self.store.read_user()

        # Build user message from SOUL/USER and any modified topic files
        content_parts: list[str] = []
        content_parts.append(f"## SOUL.md\n{soul or '(empty)'}")
        content_parts.append(f"## USER.md\n{user or '(empty)'}")

        if modified_files:
            for rel_path in modified_files:
                full_path = self.store.memory_dir / rel_path
                try:
                    text = full_path.read_text(encoding="utf-8")
                    content_parts.append(f"## {rel_path}\n{text}")
                except OSError:
                    continue

        if not soul and not user and not modified_files:
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
                        "content": "\n\n".join(content_parts),
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

        parsed = self._parse_json_output(raw, required_key="suggestions")
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
            elif modified_files and file_name in modified_files:
                file_path = self.store.memory_dir / file_name
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
