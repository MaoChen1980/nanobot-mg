"""MemoryExtractor — cron-scheduled memory extraction from saved prompts (.pt files).

Replaces the old Consolidator + Dream two-stage pipeline.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.llm_context import chat_stream_with_retry
from nanobot.utils.helpers import ensure_dir
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.agent.memory_store import MemoryStore


_SESSION_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]")
_SANITIZE_MAX_LEN = 64

_ANALYSIS_MAX_CHARS = 500_000  # Max chars of .pt content sent to analysis LLM
_USER_FEEDBACK_FILE = Path.home() / ".nanobot" / "self_improve" / "user_corrections.jsonl"

_TS_RE = re.compile(r"<!--ts:(\d+(?:\.\d+)?)-->")  # embedded timestamp in memory files

# Type → emoji for MEMORY.md index and finding formatting
_FTYPE_EMOJI: dict[str, str] = {
    "pitfall": "⚠️",
    "pattern": "💡",
    "knowledge": "📌",
    "preference": "👤",
    "instruction": "",
    "skill": "🛠️",
}
_EMOJI_SET = set(_FTYPE_EMOJI.values()) - {""}


def _parse_ts(ts_str: str | None) -> float | None:
    """Parse ISO 8601 timestamp string to float, or return None."""
    if not ts_str:
        return None
    # Normalize: handle non-standard ISO 8601 where time uses dashes
    # (e.g. "2026-06-06T10-30-00" from save_prompt_snapshot)
    normalized = re.sub(r"(?<=T)(\d{2})-(\d{2})-(\d{2})", r"\1:\2:\3", ts_str)
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return None


def _format_ts(ts: float) -> str:
    """Format float timestamp to ISO 8601 string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """Three-step memory processor: extract findings from .pt files, then write + cleanup, then index.

    Step 1 — Extract: process saved prompts, call LLM to find new information.
    Step 2 — Write + Cleanup: write findings to files, cleanup-check SOUL.md/USER.md.
    Step 3 — Post-process: materialize skills, consolidate memory, index rebuild, git commit.
    """

    def __init__(
        self,
        store: MemoryStore,
        timezone: str | None = None,
    ):
        self.store = store
        self.timezone = timezone
        self.prompts_dir = ensure_dir(store.workspace / "prompts")
        self.failed_dir = ensure_dir(self.prompts_dir / "failed")
        self.processed_dir = ensure_dir(self.prompts_dir / "processed")
        self.events_dir = ensure_dir(store.workspace / "memory" / "events")
        self._last_modified_files: list[str] = []
        self._pending_tool_scripts: list[dict[str, Any]] = []
        self._pending_skill_entries: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        """Step 1 (extract) → Step 2 (write + cleanup) → Step 3 (post-process)."""
        all_findings: list[dict[str, Any]] = []
        all_events: list[dict[str, Any]] = []

        # ── Step 1: collect .pt + .pt.processing (crash survivors) ──
        pt_files = sorted(
            p for p in self.prompts_dir.iterdir()
            if p.suffix == ".pt" or p.name.endswith(".pt.processing")
        )

        processed: list[Path] = []  # .pt.processing files that succeeded

        for pt_path in pt_files:
            # Claim the file: rename .pt → .pt.processing if needed
            if pt_path.suffix == ".pt":
                processing_path = pt_path.with_suffix(".pt.processing")
                try:
                    pt_path.rename(processing_path)
                except OSError:
                    logger.warning("MemoryExtractor: race on {}, skipping", pt_path)
                    continue
            else:
                processing_path = pt_path  # already .pt.processing, retry it

            try:
                content = json.loads(processing_path.read_text(encoding="utf-8"))
                saved_at = content.get("saved_at", "")
                analysis = await self._analysis_llm(content)
                if analysis:
                    findings = analysis.get("findings", [])
                    code_ts = _parse_ts(saved_at) or time.time()
                    for f in findings:
                        if not f.get("ts"):
                            f["ts"] = _format_ts(code_ts)
                    if findings:
                        all_findings.extend(findings)
                        logger.info(
                            "MemoryExtractor: {} findings from {}",
                            len(findings),
                            processing_path.name,
                        )
                    events = analysis.get("events", [])
                    if events:
                        for e in events:
                            if not e.get("date"):
                                e["date"] = saved_at[:10] if saved_at else _format_ts(code_ts)[:10]
                        all_events.extend(events)
                        logger.info(
                            "MemoryExtractor: {} events from {}",
                            len(events),
                            processing_path.name,
                        )
                processed.append(processing_path)
            except Exception:
                logger.exception("MemoryExtractor: failed to process {}", processing_path)
                if processing_path.is_file():
                    self.failed_dir.mkdir(parents=True, exist_ok=True)
                    failed_name = processing_path.name.replace(".pt.processing", ".pt")
                    processing_path.rename(self.failed_dir / failed_name)

        # ── User feedback processing: aggregate corrections from SelfDetectHook ──
        feedback_written = await self._process_user_feedback()

        if not all_findings and not all_events and not feedback_written:
            logger.info("MemoryExtractor: nothing to process")
            self._move_processed(processed)
            return False

        # ── Step 2: write findings + events + cleanup, then flush ──
        wrote_findings = await self._write_cleanup_and_rebuild(all_findings)
        wrote_events = await self._write_events(all_events)

        # ── Step 3: post-process ──
        changed = wrote_findings or wrote_events or self._memory_dir_changed() or feedback_written

        if await self._materialize_tool_scripts():
            changed = True
        if await self._materialize_skills():
            changed = True
        if await self._consolidate_memory():
            changed = True
            current_state = self._snapshot_memory_dir(self.store.memory_dir)
            for rel_path in current_state:
                if rel_path not in self._last_modified_files:
                    self._last_modified_files.append(rel_path)
        if changed:
            await self._cleanup_check(modified_files=self._last_modified_files)

        if changed:
            self._generate_memory_index()
            await self._rebuild_indexes()
            if self.store.git.is_initialized():
                self.store.git.auto_commit("memory: extract and cleanup")

        # ── Done: move processed .pt files ──
        self._move_processed(processed)
        return True

    def _move_processed(self, processing_paths: list[Path]) -> None:
        """Move .pt.processing files to processed/ directory."""
        for p in processing_paths:
            if not p.is_file():
                continue
            processed_name = p.name.replace(".pt.processing", ".pt")
            p.replace(self.processed_dir / processed_name)
        logger.info("MemoryExtractor: moved {} file(s) to processed/", len(processing_paths))

    async def _process_user_feedback(self) -> bool:
        """Read user_corrections.jsonl and write aggregated feedback to memory.

        SelfDetectHook writes correction/rejection signals to
        ``user_corrections.jsonl``. This method aggregates them into a
        structured markdown file in the memory directory, so ContextBuilder
        can inject patterns into the system prompt.

        Returns True if feedback was written.
        """
        if not _USER_FEEDBACK_FILE.exists():
            return False
        try:
            lines = _USER_FEEDBACK_FILE.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return False

        entries: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not entries:
            return False

        # Aggregate by matched keyword — "user corrected about X"
        from collections import Counter
        keyword_counts: Counter = Counter(e.get("matched", "") for e in entries)
        type_counts: Counter = Counter(e.get("type", "") for e in entries)
        total = len(entries)

        # Build structured summary
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [
            "## User Corrections",
            "",
            f"Aggregated from {total} correction/rejection signal(s) @ {now}.",
            "",
        ]
        if type_counts:
            parts.append("### By Type")
            for t, c in type_counts.most_common():
                parts.append(f"- **{t}**: {c}")
            parts.append("")
        if keyword_counts:
            parts.append("### Repeated Patterns (≥2)")
            for kw, c in [(k, v) for k, v in keyword_counts.most_common() if v >= 2]:
                parts.append(f"- \"{kw}\": {c}")
            parts.append("")

        # Additional detail: most recent 10 unique corrections
        seen: set[str] = set()
        detail_lines: list[str] = []
        for e in reversed(entries):
            ctx = e.get("context", "")
            if ctx and ctx not in seen:
                seen.add(ctx)
                detail_lines.append(f"- [{e['type']}] \"{e['matched']}\" → {ctx}")
            if len(detail_lines) >= 10:
                break
        if detail_lines:
            parts.append("### Recent Details")
            parts.extend(detail_lines)
            parts.append("")

        feedback_path = self.store.workspace / "framework" / "user_feedback.md"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text("\n".join(parts), encoding="utf-8")
        logger.info("MemoryExtractor: wrote workspace/framework/user_feedback.md ({} signals)", total)
        return True

    async def _analysis_llm(
        self, pt_content: dict
    ) -> dict[str, Any] | None:
        """Call LLM to analyze a saved prompt, return parsed JSON."""
        # Strip system prompt and instructions before analysis — they are
        # framework overhead, not conversation content the LLM should analyze.
        msgs = pt_content.get("messages", [])
        filtered_msgs = list(msgs)
        if filtered_msgs and filtered_msgs[0].get("role") == "system":
            filtered_msgs = filtered_msgs[1:]
        if (filtered_msgs
                and filtered_msgs[0].get("role") == "user"
                and isinstance(filtered_msgs[0].get("content"), str)
                and filtered_msgs[0]["content"].startswith("## Instructions")):
            filtered_msgs = filtered_msgs[1:]
        analysis_content = dict(pt_content)
        analysis_content["messages"] = filtered_msgs

        # Prepend saved_at so LLM knows when this conversation happened
        saved_at = pt_content.get("saved_at", "")
        pt_text = json.dumps(analysis_content, ensure_ascii=False, indent=2)
        if len(pt_text) > _ANALYSIS_MAX_CHARS:
            pt_text = "... (conversation start truncated)\n" + pt_text[-_ANALYSIS_MAX_CHARS:]

        user_content = (
            f"[Snapshot saved at: {saved_at}]\n"
            f"[Each message may contain its own timestamp field.]\n\n"
            f"{pt_text}"
        )

        ws_path = self.store.workspace.expanduser().resolve().as_posix()
        prompt = render_template("agent/extractor_analysis.md", workspace_path=ws_path)

        try:
            response = await chat_stream_with_retry(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception:
            logger.exception("MemoryExtractor: analysis LLM call failed")
            return None

        if response.finish_reason == "error":
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
            key_value = result.get(required_key)
            if key_value is not None and not isinstance(key_value, list):
                logger.warning("MemoryExtractor: '{}' is not a list, resetting", required_key)
                result[required_key] = []
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

    async def _write_cleanup_and_rebuild(self, findings: list[dict[str, Any]]) -> bool:
        """Build in-memory state, chain supersede, then full-file atomic rewrite.

        Returns True if any findings were written, False otherwise.
        """
        # Reset per-cycle pending collections — entries filled below as we iterate findings
        self._pending_skill_entries = []
        self._pending_tool_scripts = []
        # memory_state: {rel_path → [{content, ts, pinned}]}
        memory_state: dict[str, list[dict[str, Any]]] = {}
        # supersedes_plan: {rel_path → {(normalized_old_text): True}}
        # Tracks supersedes targets that need to be removed from existing file content
        supersedes_plan: dict[str, dict[str, bool]] = {}

        for finding in findings:
            ftype = finding.get("type", "skip")
            if ftype == "skip":
                continue

            content = (finding.get("content") or "").strip()
            if not content:
                continue
            # Quality gate: reject vague Chinese advice
            if re.match(r"^[-*—\s]*(注意|建议|需要|应该|可以|最好|不要)[：:]\s*[^，。]*[的了能率性力]$", content):
                logger.debug("MemoryExtractor: skipped vague finding: {}", content[:60])
                continue
            if re.match(r"^[-*—\s]*(优化|改进|提升|增强|重构|修复|完成|实现)了?\s*\w{0,8}$", content):
                logger.debug("MemoryExtractor: skipped vague finding: {}", content[:60])
                continue

            ts_raw = finding.get("ts", "")
            ts_num = _parse_ts(ts_raw) or time.time()
            pinned = finding.get("pinned") is True
            recent = finding.get("recent") is True
            paragraph = self._format_finding_paragraph(ftype, content)
            # Append ts marker
            paragraph += f"\n<!--ts:{ts_num}-->"
            if pinned:
                paragraph += "\n<!--pinned-->"
            if recent:
                paragraph += "\n<!--recent-->"

            if ftype == "preference":
                rel_path = "user.md"
                memory_state.setdefault(rel_path, []).append({
                    "content": paragraph, "ts": ts_num, "pinned": pinned,
                })

            elif ftype == "skill":
                name = (finding.get("name") or "").strip()
                if name and content:
                    skill_line = f"- **{name}**: {content}\n<!--ts:{ts_num}-->"
                    self._pending_skill_entries.append({
                        "content": skill_line, "ts": ts_num, "pinned": False,
                    })

            elif ftype in ("knowledge", "pitfall", "pattern"):
                topic = (finding.get("topic") or "").strip()
                if not topic:
                    continue
                if ftype == "pattern":
                    name = (finding.get("name") or "").strip()
                    if name:
                        paragraph = self._format_finding_paragraph(ftype, f"**{name}**: {content}")
                rel_path = self._topic_to_filepath(topic) + ".md"

                supersedes = (finding.get("supersedes") or "").strip()
                if supersedes:
                    # Try in-memory chain first
                    replaced = self._supersedes_in_memory(
                        memory_state, rel_path, supersedes, paragraph, ts_num,
                    )
                    if replaced:
                        continue
                    # Fallback: mark for file-level replacement at flush time
                    supersedes_plan.setdefault(rel_path, {})[supersedes.lower()] = True

                memory_state.setdefault(rel_path, []).append({
                    "content": paragraph, "ts": ts_num, "pinned": pinned,
                })

            elif ftype == "instruction":
                rel_path = "RULES.md"
                memory_state.setdefault(rel_path, []).append({
                    "content": paragraph, "ts": ts_num, "pinned": pinned,
                })

            elif ftype == "tool_script":
                self._pending_tool_scripts.append(finding)

            else:
                logger.warning("MemoryExtractor: unknown finding type '{}', dropped", ftype)

        if not memory_state:
            logger.info("MemoryExtractor: no actionable findings to write")
            return False

        # ── Sort each topic by ts ──
        for entries in memory_state.values():
            entries.sort(key=lambda e: e["ts"])

        # ── Flush each topic: full file rewrite (skip pending_skills.md — kept in memory) ──
        needs_content_consolidation: list[str] = []  # files with ≥3 new entries
        for rel_path, entries in memory_state.items():
            if rel_path == "pending_skills.md":
                continue
            content_lines: list[str] = []
            existing_paragraphs: list[dict[str, Any]] = []
            full_path = self.store.rules_file if rel_path == "RULES.md" else self.store.user_file if rel_path == "user.md" else self.store.memory_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)

            if full_path.exists():
                text = full_path.read_text(encoding="utf-8")
                # Parse existing paragraphs with their ts markers
                existing_paragraphs = self._parse_file_paragraphs(text)

            # Merge: remove superseded paragraphs from existing
            plan = supersedes_plan.get(rel_path, {})
            if plan:
                new_max_ts = max(e["ts"] for e in entries) if entries else 0
                kept: list[dict[str, Any]] = []
                for ep in existing_paragraphs:
                    ep_lower = ep["content"].lower()
                    if any(target in ep_lower for target in plan):
                        # Only remove if new content is actually newer
                        if ep["ts"] is None or new_max_ts > ep["ts"]:
                            logger.debug(
                                "MemoryExtractor: supersedes plan removed '{}' from {}",
                                list(plan.keys())[0][:60], rel_path,
                            )
                            continue
                    kept.append(ep)
                existing_paragraphs = kept

            # Build header + footer from existing file
            header, footer = self._parse_file_structure(text if full_path.exists() else "")
            if not header:
                header = f"# {Path(rel_path).stem}\n"
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Merge all entries: existing (already filtered) + new
            merged: list[dict[str, Any]] = list(existing_paragraphs) + [
                {"content": e["content"], "ts": e["ts"]} for e in entries
            ]

            # Dedup by normalized content (strip ts/pinned/recent markers + emoji)
            # Sort newest-first so dedup keeps the latest version
            seen: set[str] = set()
            unique: list[dict[str, Any]] = []
            for e in sorted(merged, key=lambda x: -(x["ts"] or 0)):
                clean = _TS_RE.sub("", e["content"]).replace("<!--pinned-->", "").replace("<!--recent-->", "").strip()
                clean = clean.lstrip("- ").strip()
                for em in _EMOJI_SET:
                    clean = clean.replace(em, "").strip()
                if clean not in seen:
                    seen.add(clean)
                    unique.append(e)
            # Restore chronological order for final output
            unique.sort(key=lambda x: x["ts"] or 0)

            # Remove orphaned subheadings (## or ### with no content after them)
            filtered: list[dict[str, Any]] = []
            for i, e in enumerate(unique):
                stripped = e["content"].lstrip()
                if stripped.startswith("## ") or stripped.startswith("### "):
                    has_content = any(
                        not (u["content"].lstrip().startswith("## ") or u["content"].lstrip().startswith("### "))
                        for u in unique[i + 1:]
                    )
                    if not has_content:
                        logger.debug("MemoryExtractor: removed orphaned heading: {}", _trim_sentence(e["content"]))
                        continue
                filtered.append(e)
            unique = filtered

            # Enhanced semantic dedup: merge near-duplicate findings
            if len(unique) > 1 and rel_path != "RULES.md":
                unique = self._dedup_semantic(unique)

            content_lines.append(header)
            content_lines.append("")

            # Add TL;DR from most important finding (skip for RULES.md — too diverse)
            if rel_path not in ("RULES.md", "user.md", "pending_skills.md"):
                tldr = self._build_tldr(unique)
                if tldr:
                    content_lines.append(tldr)
                    content_lines.append("")

            for e in unique:
                content_lines.append(e["content"])
                content_lines.append("")
            content_lines.append(f"---\n\n*更新: {date_str}*\n")

            # Atomic write via .tmp file
            tmp_path = full_path.with_suffix(".md.tmp")
            tmp_path.write_text("\n".join(content_lines).strip() + "\n", encoding="utf-8")
            tmp_path.replace(full_path)

            logger.info("MemoryExtractor: wrote {} paragraph(s) to {}", len(unique), rel_path)

            # Mark for content consolidation if this batch added ≥3 new entries
            if len(entries) >= 3 and rel_path not in ("RULES.md", "user.md", "pending_skills.md"):
                needs_content_consolidation.append(rel_path)

        # ── Content-level consolidation for fragmented files ──
        for rel_path in needs_content_consolidation:
            try:
                await self._consolidate_topic_content(rel_path)
            except Exception:
                logger.exception("MemoryExtractor: content consolidation failed for {}", rel_path)

        self._last_modified_files = list(memory_state.keys())
        return True

    @staticmethod
    def _parse_file_paragraphs(text: str) -> list[dict[str, Any]]:
        """Split file text into paragraphs, extracting ts from markers.

        Only the first ``# `` line is treated as the heading and excluded.
        Blockquote lines (``> ...``, e.g. TL;DR) are also excluded.
        A trailing footer block (``---`` separator + companion line) is excluded.
        """
        raw_paragraphs = re.split(r"\n\n+", text.strip())
        result: list[dict[str, Any]] = []
        heading_skipped = False
        in_footer = False
        for p in raw_paragraphs:
            p = p.strip()
            if not p:
                continue
            if not heading_skipped and p.startswith("# "):
                heading_skipped = True
                continue
            if p.startswith("> "):  # Skip blockquote (TL;DR)
                continue
            # Footer starts at --- and absorbs one trailing paragraph
            if p == "---":
                in_footer = True
                continue
            if in_footer:
                in_footer = False  # absorbed the trailing paragraph
                continue
            ts_match = _TS_RE.search(p)
            ts_val = float(ts_match.group(1)) if ts_match else 0.0
            result.append({"content": p, "ts": ts_val})
        return result

    @staticmethod
    def _parse_file_structure(text: str) -> tuple[str, str]:
        """Extract header (first # line) and footer (--- ...) from a file."""
        header = ""
        footer = ""
        lines = text.strip().split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# "):
                header = line
                break
        # Find last --- separator
        sep_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "---":
                sep_idx = i
                break
        if sep_idx >= 0:
            footer = "\n".join(lines[sep_idx:])
        return header, footer

    @staticmethod
    def _supersedes_in_memory(
        memory_state: dict[str, list[dict[str, Any]]],
        rel_path: str,
        old_text: str,
        new_paragraph: str,
        new_ts: float,
    ) -> bool:
        """Search memory_state for old_text and replace. Respects ts ordering."""
        entries = memory_state.get(rel_path, [])
        old_lower = old_text.lower()
        for entry in entries:
            content_clean = _TS_RE.sub("", entry["content"]).strip().lower()
            if old_lower in content_clean:
                if entry["ts"] is not None and new_ts <= entry["ts"]:
                    logger.debug(
                        "MemoryExtractor: supersedes skipped (old {:.3f} >= new {:.3f}): {}",
                        entry["ts"], new_ts, old_text[:60],
                    )
                    return True  # claimed but not applied — old is newer or equal
                entry["content"] = new_paragraph
                entry["ts"] = new_ts
                logger.debug("MemoryExtractor: supersedes in-memory: {} → {}", old_text[:60], new_paragraph[:60])
                return True
        return False

    @staticmethod
    def _build_tldr(unique: list[dict[str, Any]], max_chars: int = 100) -> str | None:
        """Build a TL;DR from the most important finding."""
        if not unique:
            return None
        # Pinned first, then newest (chronologically last since sorted asc)
        best = next((e for e in unique if "<!--pinned-->" in e["content"]), unique[-1])
        clean = _TS_RE.sub("", best["content"]).strip()
        # Strip ts/pinned/recent markers (they become orphaned newlines after ts removal)
        clean = clean.replace("<!--pinned-->", "").replace("<!--recent-->", "").strip()
        # Strip markdown list prefix + emoji
        clean = clean.lstrip("- ").strip()
        for e in _EMOJI_SET:
            clean = clean.replace(e, "").strip()
        if not clean:
            return None
        return "> **TL;DR**: " + _trim_sentence(clean, max_chars)

    @staticmethod
    def _tokenize_for_dedup(text: str) -> set[str]:
        """Tokenize text for semantic dedup comparison.

        Uses character bigrams of CJK text for Chinese/Japanese-heavy content,
        word-level tokens otherwise.
        """
        stripped = _TS_RE.sub("", text)
        # Use CJK character bigrams if enough Chinese/Japanese characters exist
        cjk_chars = [c for c in stripped if '一' <= c <= '鿿']
        if len(cjk_chars) >= 4:  # at least 2 bigrams for meaningful comparison
            return {cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)}
        # Word-level tokenization for Latin text
        return set(
            w.lower() for w in stripped.split()
            if len(w) > 2
        )

    @staticmethod
    def _dedup_semantic(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge near-duplicate findings within a topic using overlap heuristic.

        Uses word overlap for Latin text, character bigram overlap for CJK text.
        """
        if len(paragraphs) < 2:
            return paragraphs
        seen_idx: set[int] = set()
        result: list[dict[str, Any]] = []
        for i, e in enumerate(paragraphs):
            if i in seen_idx:
                continue
            tokens_i = MemoryExtractor._tokenize_for_dedup(e["content"])
            result.append(e)
            for j in range(i + 1, len(paragraphs)):
                if j in seen_idx:
                    continue
                tokens_j = MemoryExtractor._tokenize_for_dedup(paragraphs[j]["content"])
                if len(tokens_i) > 3 and len(tokens_j) > 3:
                    overlap = len(tokens_i & tokens_j) / max(len(tokens_i), len(tokens_j))
                    if overlap > 0.7:
                        seen_idx.add(j)
        return result

    # ------------------------------------------------------------------
    # Supersedes — replace old content with new content using FAISS
    # ------------------------------------------------------------------

    @staticmethod
    def _format_finding_paragraph(ftype: str, content: str) -> str:
        """Format a finding as a markdown paragraph with type-appropriate emoji."""
        emoji = _FTYPE_EMOJI.get(ftype, "")
        prefix = f"- {emoji} " if emoji else "- "
        return prefix + content

    # (supersedes is now handled by _supersedes_in_memory + flush-time plan)

    # ------------------------------------------------------------------
    # Events — write events to events/{topic}.md
    # ------------------------------------------------------------------

    async def _write_events(self, events: list[dict[str, Any]]) -> bool:
        """Write extracted events to ``events/{topic}.md`` Timeline section.

        Groups events by topic, appends to existing Timeline, dedup by summary.
        Returns True if any events were written.
        """
        if not events:
            return False

        # Group by topic
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            topic = (e.get("topic") or "").strip()
            if not topic:
                continue
            by_topic.setdefault(topic, []).append(e)

        for topic, topic_events in by_topic.items():
            safe_name = MemoryExtractor._sanitize_filename(topic.replace("/", "_"))
            path = self.events_dir / f"{safe_name}.md"

            async with self.store.events_lock:
                # Read existing content
                existing_timeline: list[str] = []
                if path.exists():
                    text = path.read_text(encoding="utf-8")
                    in_timeline = False
                    for line in text.split("\n"):
                        if line.strip() == "## Timeline":
                            in_timeline = True
                            continue
                        if in_timeline:
                            if line.startswith("## ") or line.startswith("---"):
                                in_timeline = False
                            elif line.strip().startswith("- "):
                                existing_timeline.append(line.strip())

                # Build existing dedup set — keyed by (summary + detail)
                existing_dedup_keys: set[str] = set()
                for line in existing_timeline:
                    colon_idx = line.find(": ")
                    if colon_idx > 0:
                        existing_dedup_keys.add(line[colon_idx + 2:].strip())

                # Append new events
                new_lines: list[str] = []
                for e in topic_events:
                    date = (e.get("date") or "").strip()[:10]
                    summary = (e.get("summary") or "").strip()
                    if not date or not summary:
                        continue
                    detail = (e.get("detail") or "").strip()
                    dedup_key = summary
                    if detail:
                        dedup_key += f" — {detail}"
                    if dedup_key in existing_dedup_keys:
                        continue
                    line = f"- {date}: {summary}"
                    if detail:
                        line += f" — {detail}"
                    new_lines.append(line)
                    existing_dedup_keys.add(dedup_key)

                if not new_lines:
                    continue

                # Find heading from topic or use topic name
                heading = topic.replace("/", " / ").replace("-", " ").title()

                # Build file content
                content_parts: list[str] = [
                    f"# {heading}",
                    "",
                    "## Timeline",
                    "",
                ]
                # Merge existing + new, sort by date
                all_entries = existing_timeline + new_lines
                all_entries.sort(key=lambda x: x[2:12] if x.startswith("- ") else "")  # sort by YYYY-MM-DD

                content_parts.extend(all_entries)
                content_parts.append("")
                content_parts.append("---")

                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(content_parts).strip() + "\n", encoding="utf-8")
                logger.info("MemoryExtractor: wrote {} event(s) to events/{}.md", len(new_lines), safe_name)

                # Compress if too many events (only if not already compressed)
                if len(all_entries) > 30:
                    current_text = path.read_text(encoding="utf-8")
                    if "<!--compressed-->" not in current_text:
                        await self._compress_events(topic, path)

        return True

    async def _compress_events(self, topic: str, path: Path) -> None:
        """Compress old events when a timeline exceeds 30 entries.

        Events older than 60 days are compressed into a quarterly summary.
        """
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # Separate recent (≤60d) and old (>60d)
        import calendar
        now = time.time()
        cutoff = now - 60 * 86400

        recent_lines: list[str] = []
        old_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            # Extract date: "- YYYY-MM-DD: ..."
            date_str = stripped[2:12].strip()
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
                if dt > cutoff:
                    recent_lines.append(stripped)
                else:
                    old_lines.append(stripped)
            except (ValueError, IndexError):
                recent_lines.append(stripped)

        if len(old_lines) < 10:
            return  # not enough old events to justify compression

        # Group old events by quarter
        quarter_groups: dict[str, list[str]] = {}
        for line in old_lines:
            date_str = line[2:12].strip()
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                quarter = f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
                quarter_groups.setdefault(quarter, []).append(line)
            except (ValueError, IndexError):
                pass

        # Build compressed timeline
        compressed: list[str] = []
        for quarter in sorted(quarter_groups.keys()):
            entries = quarter_groups[quarter]
            count = len(entries)
            compressed.append(f"- {quarter}: {count} 个事件")

        # Rebuild file: heading + timeline + compressed old + recent
        heading = content.split("\n")[0] if content.startswith("#") else f"# {topic}"
        new_content = [
            heading,
            "",
            "<!--compressed-->",
            "",
            "## Timeline",
            "",
            *compressed,
            "",
            "### Recent",
            "",
            *recent_lines,
            "",
            "---",
        ]
        path.write_text("\n".join(new_content).strip() + "\n", encoding="utf-8")
        logger.info("MemoryExtractor: compressed events/{} ({} old → {} quarterly)", path.name, len(old_lines), len(compressed))

    # ------------------------------------------------------------------
    # Tool/script materialization — tool_script findings → tools/ + pending_skills.md
    # ------------------------------------------------------------------

    async def _materialize_tool_scripts(self) -> bool:
        """Process tool_script findings: save to workspace/tools/ and enqueue skills.

        For ``script`` type: copy file to ``workspace/tools/<name>/``.
        For ``system`` type: no script to copy.
        Both types write a readme.md to ``workspace/tools/<name>/`` and append a
        skill entry to ``self._pending_skill_entries`` so downstream
        ``_materialize_skills()`` can create a full SKILL.md.

        Returns True if any changes were made.
        """
        if not self._pending_tool_scripts:
            return False

        tools_dir = ensure_dir(self.store.workspace / "tools")

        # Build set of existing tool dirs for dedup
        existing_tools: set[str] = set()
        if tools_dir.is_dir():
            for child in tools_dir.iterdir():
                if child.is_dir():
                    existing_tools.add(child.name)

        changed = False

        for ts in self._pending_tool_scripts:
            name = (ts.get("name") or "").strip()
            tool_type = ts.get("tool_type", "system")
            description = ts.get("description", "") or ""
            install_hint = ts.get("install_hint", "") or ""
            uninstall_hint = ts.get("uninstall_hint", "") or ""
            usage = ts.get("usage", "") or ""
            if not name:
                continue

            # Dedup: skip if already registered
            if name in existing_tools:
                logger.debug("MemoryExtractor: tool {} already exists, skipping", name)
                continue

            # ── Ensure workspace/tools/<name>/ exists ──
            tool_dir = tools_dir / name
            tool_dir.mkdir(parents=True, exist_ok=True)
            existing_tools.add(name)

            # ── Script type: copy file to tools dir ──
            if tool_type == "script":
                script_path_str = (ts.get("script_path") or "").strip()
                if script_path_str:
                    src = Path(script_path_str)
                    if src.exists():
                        dest = tool_dir / src.name
                        shutil.copy2(src, dest)
                        logger.info(
                            "MemoryExtractor: saved script {} → workspace/tools/{}/{}",
                            src.name, name, src.name,
                        )
                    else:
                        logger.warning(
                            "MemoryExtractor: script_path '{}' not found, skipping copy for tool '{}'",
                            script_path_str, name,
                        )

            # ── Write readme.md (both types) ──
            readme_parts: list[str] = [
                f"# {name} — {description}",
                "",
            ]
            if install_hint:
                readme_parts.extend([
                    "## Install",
                    install_hint,
                    "",
                ])
            if uninstall_hint:
                readme_parts.extend([
                    "## Uninstall",
                    uninstall_hint,
                    "",
                ])
            if usage:
                readme_parts.extend([
                    "## Usage",
                    f"    {usage}",
                    "",
                ])
            (tool_dir / "readme.md").write_text(
                "\n".join(readme_parts), encoding="utf-8"
            )
            logger.info("MemoryExtractor: wrote workspace/tools/{}/readme.md", name)

            # ── Append skill entry to _pending_skill_entries (no disk I/O) ──
            meta_parts = []
            if install_hint:
                meta_parts.append(f"Install: {install_hint}")
            if uninstall_hint:
                meta_parts.append(f"Uninstall: {uninstall_hint}")
            if usage:
                meta_parts.append(f"Usage: {usage}")
            meta_str = " | ".join(meta_parts)
            skill_line = f"- **{name}**: {description} — {meta_str}" if meta_str else f"- **{name}**: {description}"

            self._pending_skill_entries.append({
                "content": f"{skill_line}\n<!--ts:{time.time()}-->",
                "ts": time.time(),
                "pinned": False,
            })

            changed = True
            logger.info("MemoryExtractor: added tool {} to pending skills", name)

        if changed:
            from nanobot.utils.gitstore import commit_workspace_changes
            commit_workspace_changes(
                self.store.workspace,
                rel_dirs=["tools"],
                message="tool: add scripts to workspace tools and enqueue skills",
            )

        self._pending_tool_scripts = []
        return changed

    # ------------------------------------------------------------------
    # Skill creation — Phase 2: pending_skills.md → skills/<name>/SKILL.md
    # ------------------------------------------------------------------

    async def _materialize_skills(self) -> bool:
        """Convert pending skill entries (in memory) to real skills via sub-agent.

        Reads from ``self._pending_skill_entries`` — populated by both the LLM
        analysis path (``_write_cleanup_and_rebuild``) and the tool/script path
        (``_materialize_tool_scripts``). No disk file is involved.

        Spawns a sub-agent with file tools to read existing skills and decide
        create/update/merge/skip. Returns True if any changes were made.
        """
        if not self._pending_skill_entries:
            return False

        pending_text = "\n".join(e["content"] for e in self._pending_skill_entries)

        # Cheap gate: if all named skills already have a directory, skip the LLM call.
        skills_dir = self.store.workspace / "skills"
        existing_skill_dirs: set[str] = set()
        if skills_dir.is_dir():
            for child in skills_dir.iterdir():
                if child.is_dir():
                    existing_skill_dirs.add(child.name.lower())

        pending_names = {
            m.group(1).lower()
            for entry in self._pending_skill_entries
            if (m := re.search(r"^\*\*([^*]+)\*\*:", entry["content"]))
        }
        if pending_names and pending_names.issubset(existing_skill_dirs):
            logger.info(
                "MemoryExtractor: all {} pending skill(s) already exist, skipping sub-agent",
                len(pending_names),
            )
            self._pending_skill_entries = []
            return False

        # Snapshot skills dir before, to detect changes after
        dir_before: set[str] = set()
        if skills_dir.is_dir():
            for child in skills_dir.iterdir():
                if child.is_dir():
                    dir_before.add(child.name)

        # Get provider from ContextVar (set by AgentLoop at startup)
        from nanobot.agent.llm_context import _llm_model, _llm_provider
        try:
            provider = _llm_provider.get()
            model = _llm_model.get()
        except LookupError:
            logger.warning(
                "MemoryExtractor: LLM provider not available, "
                "skipping skill materialization"
            )
            self._pending_skill_entries = []
            return False

        from nanobot.agent.runner import AgentRunner, AgentRunSpec
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.search import GlobTool, GrepTool
        from nanobot.agent.tools.shell.shell import ExecTool

        tools = ToolRegistry()
        tools.register(ReadFileTool(workspace=self.store.workspace))
        tools.register(WriteFileTool(workspace=self.store.workspace))
        tools.register(EditFileTool(workspace=self.store.workspace))
        tools.register(GlobTool(workspace=self.store.workspace))
        tools.register(GrepTool(workspace=self.store.workspace))
        tools.register(ExecTool(
            working_dir=str(self.store.workspace),
            timeout=120,
        ))

        ws_path = self.store.workspace.expanduser().resolve().as_posix()
        system_prompt = render_template(
            "agent/extractor_skill_creator.md",
            workspace_path=ws_path,
        )

        user_content = (
            f"## Pending skill entries\n\n{pending_text}\n\n"
            "以上是 MemoryExtractor 从对话快照中提取的待处理 skill 需求。"
            "请按以下步骤处理每个 candidate：\n\n"
            "1. 用 glob 检查 workspace/skills/ 下已有 skill\n"
            "2. 对每个 candidate，如果有同名或功能相似的已有 skill，"
            "用 read_file 读完整内容对比\n"
            "3. 参考 skill-manager 流程决策：新建 / 更新 / 合并 / 跳过\n"
            "4. 用 write_file/edit_file 执行决策，直接写 SKILL.md\n"
            "5. 完成后清理 pending_skills.md 中已处理的条目"
        )

        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            tools=tools,
            model=model,
            max_iterations=50,
            max_tool_result_chars=10000,
        )

        runner = AgentRunner(provider)
        result = await runner.run(spec)

        if result.final_content:
            logger.info(
                "Skill materialization sub-agent: {}",
                result.final_content[:200],
            )

        # ── Done: reset for next cycle ──
        self._pending_skill_entries = []

        # Detect changes: new skill dirs
        dir_after: set[str] = set()
        if skills_dir.is_dir():
            for child in skills_dir.iterdir():
                if child.is_dir():
                    dir_after.add(child.name)

        return bool(dir_before ^ dir_after)

    @staticmethod
    def _extract_json_from_llm_output(text: str) -> str:
        """Extract JSON from LLM output that may contain <think> tags and markdown fences."""
        # Step 1: isolate content after </think> (the actual JSON output)
        think_end = text.find("</think>")
        if think_end >= 0:
            after_think = text[think_end + len("</think>"):].strip()
        else:
            after_think = text.strip()
            after_think = after_think.removeprefix("<think>").strip()
            if not after_think:
                return ""
        # Step 2: narrow search region — prefer content inside ``` fences
        fence_start = after_think.find("```")
        if fence_start >= 0:
            nl = after_think.find("\n", fence_start)
            search_in = after_think[nl + 1:].strip() if nl >= 0 else after_think
        else:
            search_in = after_think
        # Step 3: find balanced { ... } by brace-depth counting.
        # Using brace matching (not first→last fence) handles nested ```
        # inside JSON string values without matching the wrong closing fence.
        for look in ('{"', '{\n', "{'", "{"):
            brace_start = search_in.find(look)
            if brace_start >= 0:
                depth = 0
                for i in range(brace_start, len(search_in)):
                    if search_in[i] == "{":
                        depth += 1
                    elif search_in[i] == "}":
                        depth -= 1
                        if depth == 0:
                            return search_in[brace_start : i + 1]
        # Step 4: return as-is and let json.loads fail if invalid
        return search_in

    # ------------------------------------------------------------------
    # Memory consolidation — merge narrow topic files
    # ------------------------------------------------------------------

    async def _consolidate_topic_content(self, rel_path: str) -> None:
        """Content-level consolidation for a single memory file.

        Reads the full file, uses LLM to group related paragraphs, merge duplicates,
        and organize into semantic sections. Preserves all ``<!--ts:-->`` metadata.
        Called when a file gets ≥3 new entries in one extraction cycle.
        """
        full_path = self.store.memory_dir / rel_path
        if not full_path.exists():
            return
        text = full_path.read_text(encoding="utf-8")
        stripped = text.strip()
        if not stripped:
            return
        lines = stripped.count("\n") + 1
        if lines < 20:
            return  # only consolidate files with enough content

        system_msg = (
            "你是一名知识库管理员，负责整理散落的笔记文件。\n\n"
            "## 第一步：内容类型诊断\n"
            "先阅读内容，判断它属于以下哪种类型，再选择对应的结构模板。如果不匹配任一类型，使用【类型 11：通用类型】。\n\n"
            "### 【类型 1：技术文档】\n"
            "内容特征：逻辑严密、参数清晰、具备操作指导性。\n"
            "结构模板：\n"
            "- 系统/功能概述\n"
            "- 架构设计/实现方案\n"
            "- 边界条件与异常处理\n\n"
            "### 【类型 2：项目经历】\n"
            "内容特征：项目进展、模块移植、轮次进度。\n"
            "结构模板（STAR 法则）：\n"
            "- 项目基本信息（时间/角色/名称）\n"
            "- 背景与目标\n"
            "- 核心行动\n"
            "- 成果与量化指标\n\n"
            "### 【类型 3：管理文档】\n"
            "内容特征：规范、流程、权责划分。\n"
            "结构模板：\n"
            "- 核心目标\n"
            "- 标准作业程序（有序列表）\n"
            "- 权责划分\n\n"
            "### 【类型 4：调查报告】\n"
            "内容特征：数据驱动、问题与建议对应。\n"
            "结构模板：\n"
            "- 核心结论\n"
            "- 现状与数据分析\n"
            "- 发现的问题\n"
            "- 改进建议\n\n"
            "### 【类型 5：简历】\n"
            "内容特征：个人技能、职业经历。\n"
            "结构模板：个人优势 → 职业经历(时间倒序) → 项目核心成果 → 学历\n\n"
            "### 【类型 6：会议纪要】\n"
            "内容特征：会议讨论、决议、待办。\n"
            "结构模板：\n"
            "- 会议基本信息（时间/人员/主题）\n"
            "- 核心决议\n"
            "- 待办事项\n\n"
            "### 【类型 7：产品需求/创意提案】\n"
            "内容特征：用户痛点、方案描述、衡量标准。\n"
            "结构模板：\n"
            "- 痛点与背景\n"
            "- 核心方案\n"
            "- 成功指标\n\n"
            "### 【类型 8：读书笔记/课程复盘】\n"
            "内容特征：知识提炼、金句、应用。\n"
            "结构模板：\n"
            "- 核心知识架构\n"
            "- 金句/颠覆性认知\n"
            "- 个人实践清单\n\n"
            "### 【类型 9：商业计划书/公司简介】\n"
            "内容特征：市场机会、商业模式、竞争壁垒。\n"
            "结构模板：\n"
            "- 一句话项目介绍\n"
            "- 市场机会与痛点\n"
            "- 商业模式与优势\n\n"
            "### 【类型 10：复盘报告/个人总结】\n"
            "内容特征：回顾目标、得失分析、改进计划。\n"
            "结构模板（KPT 模型）：\n"
            "- 目标回顾与现状\n"
            "- 保持（Keep）\n"
            "- 问题（Problem）\n"
            "- 尝试（Try）\n\n"
            "### 【类型 11：经验集合 / 通用类型（兜底）】\n"
            "内容特征：多条独立发现/经验的集合，无统一叙述；或上述类型均不匹配。\n"
            "结构模板：先尝试按语义分组归类；无关联则保持原有条目。\n"
            "- 核心摘要（最重要的 1-2 条）\n"
            "- ## 分组整理\n"
            "- 核心要点提炼\n\n"
            "## 第二步：重组规则\n"
            "1. 合并重复或高度重叠的条目，保留信息最完整的那条。合并时可微调措辞以消除冗余，但不要改变技术含义\n"
            "2. **必须保留所有 HTML 注释元数据** — 每个段落后的 `<!--ts:数位戳-->`（如 `<!--ts:1782084120.0-->`）、`<!--pinned-->`、`<!--recent-->` 不能丢失或改变\n"
            "3. 保持原始措辞与技术细节，不要润色或重写技术内容\n"
            "4. 保留文件末尾的 --- 分隔线和 *更新: 日期* 脚注\n\n"
            "输出完整的 markdown 文件内容，不要用代码块包裹。"
        )

        response = await chat_stream_with_retry(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"请重组这个记忆文件的内容：\n\n{text}"},
            ],
        )
        if response.finish_reason == "error":
            logger.warning("MemoryExtractor: content consolidation LLM error for {}", rel_path)
            return

        new_text = (response.content or "").strip()
        if not new_text:
            return

        # Extract content inside markdown code block if LLM wrapped it
        if new_text.startswith("```"):
            lines_in = new_text.split("\n")
            start = 1 if lines_in[0].startswith("```") else 0
            end = -1 if lines_in[-1].strip().startswith("```") else len(lines_in)
            new_text = "\n".join(lines_in[start:end]).strip()

        # Verify: LLM must not drop all ts markers (some merging is expected)
        old_ts_count = stripped.count("<!--ts:")
        new_ts_count = new_text.count("<!--ts:")
        if new_ts_count == 0 and old_ts_count > 0:
            logger.warning(
                "MemoryExtractor: content consolidation dropped too many ts markers "
                "({} → {}), skipping write for {}", old_ts_count, new_ts_count, rel_path
            )
            return

        # Atomic write
        tmp_path = full_path.with_suffix(".md.tmp")
        tmp_path.write_text(new_text + "\n", encoding="utf-8")
        tmp_path.replace(full_path)
        logger.info("MemoryExtractor: content consolidated {} ({} lines → {})", rel_path, lines, new_text.count("\n") + 1)

    async def _rebuild_indexes(self) -> None:
        """Regenerate index.md, tree.json, and rebuild FAISS indexes.
        (MEMORY.md is generated by run() which has recent_entries.)
        """
        self._generate_index_files()
        self._generate_tree_json()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.store.build_vector_index)
        await loop.run_in_executor(None, self.store.build_skills_index)

    async def _consolidate_memory(self) -> bool:
        """Consolidate memory files: merge small files, reorganize dirs under 20 limit.

        Supports three operations:
        - ``merge_files``: combine small related files within one directory
        - ``merge_dirs``: merge a whole directory into another (files moved, source removed)
        - ``move_file``: move a single file to a different directory (topic rename)

        Returns True if any changes were executed.
        """
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md", "pending_skills.md", "lessons.md", "self_mod.md", "system.md"}

        # Single pass: collect all file metadata (lines, heading) and topic structure
        file_meta: dict[str, dict[str, Any]] = {}
        all_topics: dict[str, list[str]] = {}
        small_candidates: dict[str, list[tuple[str, int]]] = {}

        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir)
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            lines = len(text.splitlines())
            heading = ""
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("## ") or s.startswith("# "):
                    heading = s
                    break

            rel_str = str(rel)
            file_meta[rel_str] = {"lines": lines, "heading": heading}
            parent = str(rel.parent)
            all_topics.setdefault(parent, []).append(rel.name)
            if parent != "." and lines <= 10:
                small_candidates.setdefault(parent, []).append((rel.name, lines))

        # Count topic directories (exclude root ".")
        topic_dirs = sorted(d for d in all_topics if d != ".")
        total_dirs = len(topic_dirs)
        over_limit = total_dirs >= 20

        has_small_clusters = any(len(v) >= 3 for v in small_candidates.values())

        if not over_limit and not has_small_clusters:
            return False

        # ── Build prompt ──
        parts = [f"你正在整理知识库的目录结构。唯一约束：目录数不超过 20。当前 {total_dirs}/20。\n"]
        if over_limit:
            parts.append("⚠️ 超过上限，需要合并！")


        parts.append("\n### 当前目录")
        for d in topic_dirs:
            files = all_topics.get(d, [])
            parts.append(f"- {d}/ ({len(files)} 个文件)")
            for name in sorted(files):
                rel_path = f"{d}/{name}" if d != "." else name
                meta = file_meta.get(rel_path, {})
                heading = meta.get("heading", "")
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
            response = await chat_stream_with_retry(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception:
            logger.exception("MemoryExtractor: consolidation LLM call failed")
            return False

        if response.finish_reason == "error":
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
        if not isinstance(operations, list):
            operations = []
        if not operations:
            logger.info("MemoryExtractor: no consolidation operations suggested")
            return False

        executed = False

        for op in operations:
            op_type = op.get("type", "")
            category = op.get("category", "")
            sources = op.get("sources", [])
            target = op.get("target", "")
            op.get("reason", "")

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
                            line for line in body_lines
                            if not line.startswith("# ") and not line.startswith("---")
                        )
                        combined.append(body.strip())
                        combined.append("")
                text = "\n".join(combined).strip()
                if not text:
                    continue
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                text += f"\n\n---\n\n*合并 Consolidation: {date_str}*"
                target_path.parent.mkdir(parents=True, exist_ok=True)
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
                        remaining_dirs = [p for p in remaining if p.is_dir()]
                        remaining_files = [p for p in remaining if p.is_file()]
                        if not remaining_files and not remaining_dirs:
                            import shutil
                            shutil.rmtree(src_dir)
                        elif remaining_files and not remaining_dirs and all(
                            p.name == "index.md" or ".vector_index" in p.parts
                            for p in remaining_files
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
                    src_path_str.split("/", 1)
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
        """Generate MEMORY.md for human browsing — knowledge map + event timeline + health stats.

        Not injected into agent context. Serves as a human-friendly table of contents.
        """
        exclude_names = {"MEMORY.md", "topic-map.json", "index.md", "tree.json"}
        now = time.time()
        stale_cutoff = now - 90 * 86400

        # ── Scan memory/ files ──
        topic_groups: dict[str, list[tuple[str, str, float]]] = {}  # topic → [(rel, h1, mtime)]
        total_files = 0
        stale_files = 0
        for p in self.store.memory_dir.rglob("*.md"):
            if ".vector_index" in p.parts or p.name in exclude_names:
                continue
            rel = p.relative_to(self.store.memory_dir).as_posix()
            try:
                text = p.read_text(encoding="utf-8")
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if not text.strip():
                continue
            total_files += 1
            if mtime < stale_cutoff:
                stale_files += 1

            h1 = ""
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("# "):
                    h1 = s.lstrip("# ").strip()[:80]
                    break

            parent = rel.rsplit("/", 1)[0] if "/" in rel else "root"
            topic_groups.setdefault(parent, []).append((rel, h1, mtime))

        # ── Scan events/ for timeline ──
        event_entries: list[str] = []
        if self.events_dir and self.events_dir.is_dir():
            for p in sorted(self.events_dir.rglob("*.md")):
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                in_timeline = False
                for line in text.split("\n"):
                    s = line.strip()
                    if s == "## Timeline":
                        in_timeline = True
                        continue
                    if in_timeline:
                        if s.startswith("## ") or s.startswith("---"):
                            break
                        if s.startswith("- ") and len(s) > 14:
                            event_entries.append(s)
                if len(event_entries) >= 20:
                    break
        event_entries.sort(reverse=True)
        event_entries = event_entries[:20]

        # ── Build MEMORY.md ──
        lines = ["# Memory", "", "> Human-readable index of the knowledge base. See also `events/` for timeline records.", ""]

        # Knowledge Map
        lines.append("## Knowledge Map\n")
        for topic in sorted(topic_groups, key=lambda t: (t != "root", t)):
            files = topic_groups[topic]
            files.sort(key=lambda x: -x[2])  # newest first
            label = topic.replace("_", " ").replace("/", " / ").title()
            count = len(files)
            descs = []
            for rel, h1, _ in files[:5]:
                display = h1 or Path(rel).stem
                descs.append(f"[{display}]({rel})")
            topic_str = "; ".join(descs)
            if count > 5:
                topic_str += f" (+{count - 5} more)"
            lines.append(f"- **{label}** ({count} files) — {topic_str}")
        lines.append("")

        # Recent Events
        if event_entries:
            lines.append("## Recent Events\n")
            lines.extend(event_entries)
            lines.append("")

        # Health Stats
        lines.append("## Health\n")
        lines.append(f"- Total knowledge files: {total_files}")
        lines.append(f"- Topic areas: {len(topic_groups)}")
        if stale_files:
            lines.append(f"- Stale files (no update in 90d): {stale_files}")
        lines.append(f"- Event entries tracked: {len(event_entries):}+")
        lines.append("")

        self.store.memory_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info(
            "MemoryExtractor: re-generated MEMORY.md — {} files in {} topics, {} events",
            total_files, len(topic_groups), len(event_entries),
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
        ws_path = self.store.workspace.expanduser().resolve().as_posix()
        soul_content = self.store.read_soul()
        user_content = self.store.read_user()

        # Build user message from SOUL/USER and any modified topic files
        content_parts: list[str] = []
        content_parts.append(f"## SOUL.md\n{soul_content or '(empty)'}")
        content_parts.append(f"## USER.md\n{user_content or '(empty)'}")

        if modified_files:
            for rel_path in modified_files:
                if rel_path == "user.md":
                    continue  # Already included as ## USER.md
                full_path = self.store.memory_dir / rel_path
                try:
                    text = full_path.read_text(encoding="utf-8")
                    content_parts.append(f"## {rel_path}\n{text}")
                except OSError:
                    continue

        if not soul_content and not user_content and not modified_files:
            return

        try:
            response = await chat_stream_with_retry(
                messages=[
                    {
                        "role": "system",
                        "content": render_template("agent/extractor_cleanup.md", workspace_path=ws_path),
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

        if response.finish_reason == "error":
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

            if file_name in ("SOUL.md", "soul.md"):
                file_path = self.store.soul_file
            elif file_name in ("USER.md", "user.md"):
                file_path = self.store.user_file
            elif modified_files:
                matched = next((f for f in modified_files if f == file_name or f.endswith("/" + file_name)), None)
                if matched:
                    file_path = self.store.memory_dir / matched
                else:
                    continue
            else:
                continue

            try:
                current = file_path.read_text(encoding="utf-8")
                if target and target in current:
                    if action == "remove":
                        new_content = current.replace(target, "", 1)
                    elif action == "rewrite" and replacement and replacement != "null":
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
        safe_parts = [p for p in safe_parts if p != ".."]  # block path traversal
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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        ts_file = ts.replace(":", "-")  # Windows forbids colons in filenames
        filename = f"{safe_key}-{ts_file}.pt"
        path = prompts_dir / filename

        payload = {
            "session_key": session_key,
            "saved_at": ts,
            "messages": messages,
        }

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Saved .pt: {} ({} msgs, session={})", filename, len(messages), session_key)
        return path
