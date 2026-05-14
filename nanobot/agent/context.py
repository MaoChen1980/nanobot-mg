"""Context builder for assembling agent prompts."""

from __future__ import annotations

import base64
import mimetypes
import platform
import re
import threading
import time
from dataclasses import dataclass
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, current_time_str, truncate_text
from nanobot.utils.media_decode import detect_image_mime
from nanobot.utils.prompt_templates import render_template

# Module-level cache for template file contents (path -> (mtime, content))
_template_content_cache: dict[str, tuple[float, str]] = {}


@dataclass
class ContextState:
    """Runtime state for LLM context assembly.

    Carries session-level parameters that change slowly across turns,
    extracted from ``build_messages()`` to reduce per-call boilerplate.
    """
    tool_definitions: list[dict[str, Any]] | None = None
    current_iteration: int | None = None
    max_iterations: int | None = None
    session_summary: str | None = None
    max_keep_rounds: int | None = None  # 0/None = keep all, no timeline


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _SECTION_SEPARATOR = "\n\n" + "═" * 72 + "\n\n"
    _RUNTIME_CONTEXT_TAG = "## Runtime Context"
    _MAX_RECENT_HISTORY = 10
    _MAX_HISTORY_CHARS = 64_000  # hard cap on recent history section size
    _RUNTIME_CONTEXT_END = "## /Runtime Context"

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None, db=None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace, db=db)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        self._bootstrap_cache: dict[str, tuple[float, str]] = {}


    def warmup(self) -> None:
        """Pre-load all file-based caches so the first build_system_prompt is fast."""
        _t0 = time.time()
        self.memory.read_memory()
        self._load_bootstrap_files()
        self.skills.build_skills_summary()
        self.skills.get_always_skills()
        # Preload embedding model in background — loading SentenceTransformer
        # synchronously blocks the event loop for ~8s, starving proxy heartbeats.
        threading.Thread(
            target=self.memory.vector_index._load_model,
            daemon=True,
        ).start()
        _elapsed = (time.time() - _t0) * 1000
        if _elapsed > 50:
            logger.info("ContextBuilder warmup took {:.0f}ms", _elapsed)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        _t0 = time.time()
        parts = [self._get_identity(channel=channel)]

        # Tools early — let LLM know capabilities before loading context
        if tool_definitions:
            section = self._build_tools_section(tool_definitions)
            if section:
                parts.append(section)

        # Memory — try vector search, fall back to MEMORY.md
        memory_section = self._build_memory_section()
        if memory_section:
            parts.append(memory_section)

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        # ── DB-sourced context (previous sessions) ──
        _db_started = False

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            # Only show entries with a Dream summary — skip RAW archives that
            # contain full message dumps including tool calls and system text.
            filtered = [e for e in entries if e.get("summary")]
            if filtered:
                capped = filtered[-self._MAX_RECENT_HISTORY:]
                history_text = "\n".join(
                    f"- [{self._convert_timestamp(e['timestamp'], self.timezone)}] {self._sanitize_md(e['summary'])}" for e in capped
                )
                history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
                _db_started = True
                db_header = "# ── Historical Context (from previous sessions) ──\n"
                parts.append(db_header)
                parts.append("# Recent History\n\n" + history_text)

        # Current State — Goals + recent events from DB, near end for recency bias
        state_block = self._build_state_section()
        if state_block:
            if not _db_started:
                parts.append("# ── Historical Context (from previous sessions) ──\n")
            parts.append(f"# Current State\n\n{state_block}")

        _elapsed = (time.time() - _t0) * 1000
        if _elapsed > 100:
            logger.info("build_system_prompt took {:.0f}ms", _elapsed)
        return self._SECTION_SEPARATOR.join(parts)

    def _build_tools_section(self, tool_definitions: list[dict[str, Any]]) -> str:
        """Build the available tools section for the system prompt."""
        if not tool_definitions:
            return ""
        lines = [
            "# Available Tools\n",
            "注意：只读工具（grep/read_file/glob 等）相同参数 60s 内重复调用返回缓存结果。"
            "任何工具连续返回相同内容会被去重为简短提示。\n",
        ]
        for schema in tool_definitions:
            fn = schema.get("function", {})
            name = fn.get("name", "unknown")
            desc = fn.get("description", "")
            # Keep up to 2500 chars so LLM sees detailed "when to use / how to
            # act / failure modes" guidance.  Truncate at \n\n boundary when
            # possible to keep whole sections.
            if len(desc) > 2500:
                truncated = desc[:2497]
                last_break = truncated.rfind("\n\n")
                if last_break > 1000:
                    desc = truncated[:last_break]
                else:
                    desc = truncated + "..."
            # Indent continuation lines for LLM-readable hierarchy
            _desc_lines = desc.split("\n")
            indented_desc = _desc_lines[0]
            for _line in _desc_lines[1:]:
                indented_desc += "\n  " + _line if _line.strip() else "\n"
            lines.append(f"- **{name}**: {indented_desc}")
            lines.append("")  # blank line between tools
        return "\n".join(lines)

    @staticmethod
    def _adjust_headings(text: str, offset: int = 1) -> str:
        """Shift all markdown heading levels by *offset*.

        Positive = demote (add #), negative = promote (remove #).
        Clamps to valid range [1, 6].
        """
        def _replace(m: re.Match) -> str:
            level = len(m.group(1))
            new_level = max(1, min(6, level + offset))
            return "#" * new_level + m.group(2)
        return re.sub(r'^(#{1,6})(\s)', _replace, text, flags=re.MULTILINE)

    @staticmethod
    def _sanitize_md(text: str) -> str:
        """Escape block-level markdown constructs in *text* for safe embedding.

        Prevents injected DB content from creating headings, horizontal rules,
        blockquotes, or code fences that would break the system prompt structure.
        """
        # Leading # (headings), ---/___/*** (horizontal rules), > (blockquotes),
        # ``` (code fence)
        text = re.sub(r'^#', r'\\#', text, flags=re.MULTILINE)
        text = re.sub(r'^(-{3,}|_{3,}|\*{3,})\s*$', r'\\\1', text, flags=re.MULTILINE)
        text = re.sub(r'^>', r'\\>', text, flags=re.MULTILINE)
        text = re.sub(r'^```', r'\\`\\`\\`', text, flags=re.MULTILINE)
        return text

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            channel=channel,
        )

    @staticmethod
    def _convert_timestamp(ts: str, timezone: str | None) -> str:
        """Convert an ISO timestamp string to the given timezone, or return as-is."""
        if not timezone or not ts:
            return ts
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from nanobot.utils.helpers import _format_datetime
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is not None:
                return _format_datetime(dt.astimezone(ZoneInfo(timezone)))
        except Exception:
            logger.warning("Failed to convert timestamp '{}' to timezone '{}'", ts, timezone)
        return ts

    @staticmethod
    def _build_message_timeline(history: list[dict], timezone: str | None = None) -> str:
        """Build a compact chronological index from user/assistant messages.

        Extracts only user and assistant messages with timestamps — tool calls
        and results are excluded. The timeline serves as a lightweight history
        index so the LLM can perceive conversation flow before the retained
        full-message window.
        """
        entries: list[str] = []
        for msg in history:
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            ts = msg.get("timestamp", "") or ""
            if ts and timezone:
                ts = ContextBuilder._convert_timestamp(ts, timezone)
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = " ".join(texts)
            if isinstance(content, str):
                content = content.replace("\n", " ").strip()
            ts_str = ts if ts else "?"
            entries.append(f"- [{ts_str}] {role}: {content}")

        if not entries:
            return ""
        return "## Message Timeline\n\n" + "\n".join(entries)

    def _build_state_section(self) -> str:
        """Build a merged Current State block from Goals + recent events.

        Note: HEARTBEAT active tasks are NOT injected here — they are embedded
        directly in heartbeat messages by the heartbeat service (service.py).
        """
        blocks = []

        # Goals — query from DB instead of file
        goals = self._query_goals_for_context()
        if goals:
            blocks.append(f"## Goals\n\n{goals}")


        # Process log — from events table instead of file
        events = self._query_recent_events()
        if events:
            blocks.append("## Recent Progress\n\n" + events)

        return "\n\n".join(blocks) if blocks else ""

    def _query_goals_for_context(self) -> str:
        """Query active goals from DB and format as text."""
        if self.memory._db is None:
            return ""
        goals = self.memory._db.list_goals(status="in_progress")
        if not goals:
            return ""
        lines = []
        for g in goals:
            project = g.get("project", "")
            project_str = f" [{project}]" if project else ""
            lines.append(f"- **{self._sanitize_md(g['title'])}**{project_str}")
            if g.get("description"):
                lines.append(f"  - {self._sanitize_md(g['description'])}")
            data = g.get("data") or {}
            if data.get("subtasks"):
                for st in data["subtasks"]:
                    status_icon = "✅" if st.get("status") == "done" else "⬜"
                    lines.append(f"  {status_icon} {self._sanitize_md(st.get('title', st.get('id', '?')))}")
        return "\n".join(lines)

    def _query_recent_events(self) -> str:
        """Query recent events from DB and format as text."""
        if self.memory._db is None:
            return ""
        events = self.memory._db.list_events(limit=5)
        if not events:
            return ""
        lines = []
        for e in reversed(events):
            ts = self._convert_timestamp(e["timestamp"], self.timezone)
            ts = ts[:26] if ts else "?"
            lines.append(f"### [{ts}] {self._sanitize_md(e['content'])}")
        return "\n".join(lines)

    # -- vector-indexed memory -------------------------------------------------

    def _build_memory_section(self) -> str:
        """Build memory section: MEMORY.md first, then FAISS vector search results."""
        parts: list[str] = []

        # 1. Always include MEMORY.md (if customized by user)
        long_term = self.memory.read_memory()
        if long_term and not self._is_template_content(long_term, "memory/MEMORY.md"):
            # Strip "# Memory" H1 (redundant — wrapper is already # Memory)
            lines = long_term.split("\n")
            if lines and lines[0].startswith("# "):
                lines = lines[1:]
            content = "\n".join(lines).strip()
            # Bump remaining headings by +1: ## 命名约定 → ### 命名约定
            content = self._adjust_headings(content, offset=1)
            parts.append(f"# Memory\n\n## Long-term Memory\n{content}")

        # 2. Append FAISS vector search results for additional relevant context
        query_parts: list[str] = []

        goals_text = self._query_goals_for_context()
        if goals_text:
            query_parts.append(goals_text)

        events_text = self._query_recent_events()
        if events_text:
            query_parts.append(events_text)

        query = "\n".join(query_parts) if query_parts else ""
        if query:
            vector_results = self.memory.vector_index.search(query, k=5)
            if vector_results:
                parts.append("# Memory (retrieved)\n\n" + self._format_vector_results(vector_results))

        return "\n\n---\n\n".join(parts) if parts else ""

    @staticmethod
    def _format_vector_results(results: list[dict]) -> str:
        """Format vector search results grouped by source file."""
        from collections import OrderedDict

        grouped: dict[str, list[dict]] = OrderedDict()
        for r in results:
            grouped.setdefault(r["source"], []).append(r)

        lines: list[str] = []
        for source, items in grouped.items():
            for item in items:
                heading = item.get("heading", "")
                score = item.get("score", 0)
                label = f"{source} — {heading}" if heading else source
                lines.append(f"**{label}** (relevance: {score:.2f})")
                text = item.get("text", "")
                if len(text) > 300:
                    text = text[:297] + "..."
                lines.append(f"> {text}\n")

        return "\n".join(lines)

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None,
        current_iteration: int | None = None,
        max_iterations: int | None = None,
        message_time: str | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = []
        if message_time:
            lines.append(f"**Current Message Time: {message_time}**")
        lines.append(f"Current Time: {current_time_str(timezone)}")
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if current_iteration is not None and max_iterations is not None:
            lines.append(f"Iteration: {current_iteration}/{max_iterations}")
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        return (
            ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" +
            "\n".join(lines) + "\n" +
            ContextBuilder._RUNTIME_CONTEXT_END +
            "\n\n--- Current Turn ---"
        )

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace (cached by file mtime).

        Falls back to the bundled template when the workspace file doesn't exist.
        """
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if not file_path.exists():
                # Fallback to bundled template
                from importlib.resources import files as pkg_files
                try:
                    tpl = pkg_files("nanobot") / "templates" / filename
                    if tpl.is_file():
                        content = tpl.read_text(encoding="utf-8")
                        parts.append(f"## {filename}\n\n{self._adjust_headings(content, offset=1)}")
                except Exception as e:
                    logger.warning("Failed to load bundled template {}: {}", filename, e)
                continue

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue

            cached = self._bootstrap_cache.get(filename)
            if cached is None or cached[0] != mtime:
                content = file_path.read_text(encoding="utf-8")
                self._bootstrap_cache[filename] = (mtime, content)
                cached = (mtime, content)

            parts.append(f"## {filename}\n\n{self._adjust_headings(cached[1], offset=1)}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if not tpl.is_file():
                return False
            mtime = tpl.stat().st_mtime
            cached = _template_content_cache.get(template_path)
            if cached is None or cached[0] != mtime:
                _template_content_cache[template_path] = (mtime, tpl.read_text(encoding="utf-8"))
                cached = _template_content_cache[template_path]
            return content.strip() == cached[1].strip()
        except Exception as e:
            logger.debug("Failed to read bundled template: {}", e)
            return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        context_state: ContextState | None = None,
        message_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        cs = context_state or ContextState()

        # ── Timeline + history truncation ──
        # Keep last N user-message rounds as full messages; put earlier
        # rounds into a compact timeline in the system prompt.
        # N = cs.max_keep_rounds (0/None = keep all, no timeline).
        max_keep = cs.max_keep_rounds or 0
        if max_keep > 0 and len(history) > 0:
            user_count = 0
            split_idx = 0
            for i in range(len(history) - 1, -1, -1):
                if history[i].get("role") == "user":
                    user_count += 1
                    if user_count >= max_keep:
                        split_idx = i
                        break
            timeline_history = history[:split_idx]
            retained_history = history[split_idx:]
        else:
            timeline_history: list[dict] = []
            retained_history = history

        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone, session_summary=cs.session_summary,
            current_iteration=cs.current_iteration,
            max_iterations=cs.max_iterations,
            message_time=message_timestamp,
        )

        # Search vector index with current message for relevant memory.
        # Nested inside the runtime-context block so _record_turn strips it.
        msg_query = current_message.strip()
        if msg_query:
            vec_results = self.memory.vector_index.search(msg_query, k=3)
            if vec_results:
                formatted = self._format_vector_results(vec_results)
                memory_block = (
                    "## Memory (current context)\n\n"
                    f"Relevant memories for the current message:\n\n{formatted}"
                )
                end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                runtime_ctx = runtime_ctx.replace(
                    end_marker, memory_block + "\n" + end_marker
                )

        user_content = self._build_user_content(current_message, media)
        if runtime_ctx:
            if isinstance(user_content, str):
                user_content = f"{runtime_ctx}\n\n{user_content}"
            else:
                user_content = [{"type": "text", "text": runtime_ctx}] + list(user_content)
        sys_prompt = self.build_system_prompt(skill_names, channel=channel, tool_definitions=cs.tool_definitions)
        if timeline_history:
            timeline = self._build_message_timeline(timeline_history, self.timezone)
            sys_prompt = f"{sys_prompt}\n\n{timeline}"
        messages = [
            {"role": "system", "content": sys_prompt},
            *retained_history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), user_content)
            messages[-1] = last
        else:
            messages.append({"role": current_role, "content": user_content})
        import json as _json
        prompt_file = self.workspace / ".prompt_dump.jsonl"
        sys_prompt = messages[0]["content"] if messages else ""
        with open(prompt_file, "a", encoding="utf-8") as _f:
            _f.write("### SYSTEM PROMPT ###\n")
            _f.write(sys_prompt)
            _f.write("\n### END SYSTEM PROMPT ###\n")
            for m in messages[-3:]:
                c = m.get("content", "")
                if isinstance(c, str):
                    c = c[:2000]
                elif isinstance(c, list):
                    c = str(c)[:2000]
                _f.write(_json.dumps({"role": m["role"], "content_snippet": c}, ensure_ascii=False) + "\n")
            _f.write("---\n")
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages
