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
from nanobot.utils.helpers import build_assistant_message, current_time_str, format_message_header
from nanobot.utils.media_decode import detect_image_mime
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.tools_index import rebuild_tools_index as _rebuild_tools_index

# Module-level cache for template file contents (path -> (mtime, content))
_template_content_cache: dict[str, tuple[float, str]] = {}
_MAX_TEMPLATE_CACHE_SIZE = 20


@dataclass
class ContextState:
    """Runtime state for LLM context assembly.

    Carries session-level parameters that change slowly across turns,
    extracted from ``build_messages()`` to reduce per-call boilerplate.
    """
    tool_definitions: list[dict[str, Any]] | None = None
    current_iteration: int | None = None
    max_iterations: int | None = None


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _SKIP_IF_DEFAULT = {"USER.md"}  # TOOLS.md is auto-generated — always inject
    _SECTION_SEPARATOR = "\n\n" + "═" * 72 + "\n\n"
    _RUNTIME_CONTEXT_TAG = "## Runtime Context"
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
        """Build the static portion of the system prompt (identity, tools, bootstrap, skills)."""
        _t0 = time.time()
        parts = [self._get_identity(channel=channel)]

        # Tools early — let LLM know capabilities before loading context
        if tool_definitions:
            section = self._build_tools_section(tool_definitions)
            if section:
                parts.append(section)

        # Regenerate tools index so TOOLS.md is always fresh
        _rebuild_tools_index(self.workspace)

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        lessons = self._load_lessons()
        if lessons:
            parts.append(lessons)

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.format_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

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
    def _shift_headings(text: str, offset: int = 1) -> str:
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
    def _escape_block_md(text: str) -> str:
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
    def _backfill_thinking_to_content(messages: list[dict]) -> list[dict]:
        """Fill empty assistant content with thinking/reasoning text.

        When a model returns only thinking (tool calls) without content, the
        content field is empty. This copies thinking into content so the LLM
        sees the thought process as conversation text on subsequent turns.
        """
        result: list[dict] = []
        for msg in messages:
            if msg.get("role") != "assistant":
                result.append(msg)
                continue

            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                result.append(msg)
                continue
            if isinstance(content, list) and content:
                result.append(msg)
                continue

            # Empty content — collect thinking from available fields
            thinking = None

            blocks = msg.get("thinking_blocks")
            if isinstance(blocks, list):
                texts = [b.get("thinking", "") for b in blocks if isinstance(b, dict) and b.get("thinking")]
                if texts:
                    thinking = " ".join(texts)

            if not thinking:
                rc = msg.get("reasoning_content")
                if isinstance(rc, str) and rc.strip():
                    thinking = rc.strip()

            if not thinking:
                rd = msg.get("reasoning_details")
                if isinstance(rd, list):
                    texts = [d.get("reasoning", "") for d in rd if isinstance(d, dict) and d.get("reasoning")]
                    if texts:
                        thinking = " ".join(texts)

            if thinking:
                msg = dict(msg)
                msg["content"] = thinking
            result.append(msg)
        return result

    def _build_task_tree_section(self) -> str:
        """Read tasks/TREE.md from the workspace for context injection."""
        tree_path = self.workspace / "tasks" / "TREE.md"
        if not tree_path.exists():
            return ""
        try:
            content = tree_path.read_text(encoding="utf-8").strip()
        except Exception:
            logger.warning("Failed to read task tree at {}", tree_path)
            return ""
        if not content:
            return ""
        return (
            "## Task Tree\n\n"
            "Current task tree. Tasks are managed as files under tasks/ — "
            "use read_file/write_file/edit_file to update them.\n\n"
            + self._shift_headings(content, offset=1)
        )

    # -- vector-indexed memory -------------------------------------------------

    def _build_memory_section(self) -> str:
        """Build memory section: MEMORY.md + key content files (no vector search)."""
        memory_dir = self.memory.memory_dir
        parts = []

        # Load MEMORY.md index
        index_content = self.memory.read_memory()
        if index_content and not self._is_default_template_content(index_content, "memory/MEMORY.md"):
            lines = index_content.split("\n")
            if lines and lines[0].startswith("# "):
                lines = lines[1:]
            index_text = "\n".join(lines).strip()
            if index_text:
                parts.append(index_text)

        # Also inline key memory files so rules/preferences are visible without recall
        for name in ("system.md", "user.md"):
            fpath = memory_dir / name
            if fpath.exists():
                text = fpath.read_text(encoding="utf-8").strip()
                if text:
                    heading = name.replace(".md", "").title()
                    parts.append(f"### {heading}\n\n{text}")

        if not parts:
            return ""
        return (
            "# Memory\n\n"
            "## Long-term Memory\n\n"
            "This is your persistent memory — facts, conventions, and patterns "
            "learned from past work. Follow these guidelines in your responses.\n\n"
            + "\n\n".join(parts)
        )

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
        channel: str | None = None,
        timezone: str | None = None,
        current_iteration: int | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [format_message_header(), f"Current Time: {current_time_str(timezone)}"]
        if channel:
            lines.append(f"Channel: {channel}")
        if current_iteration is not None and max_iterations is not None:
            lines.append(f"Iteration: {current_iteration}/{max_iterations}")
        return (
            ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" +
            "\n".join(lines) + "\n" +
            ContextBuilder._RUNTIME_CONTEXT_END +
            "\n\n--- latest user message below ---"
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
        Files in _SKIP_IF_DEFAULT that haven't been customized are omitted.
        """
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if not file_path.exists():
                if filename in self._SKIP_IF_DEFAULT:
                    continue  # no bundled fallback for placeholder forms
                # Fallback to bundled template
                from importlib.resources import files as pkg_files
                try:
                    tpl = pkg_files("nanobot") / "templates" / filename
                    if tpl.is_file():
                        content = tpl.read_text(encoding="utf-8")
                        parts.append(f"## {filename}\n\n{self._shift_headings(content, offset=1)}")
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
                # Skip if user hasn't customized this file (still default template)
                if filename in self._SKIP_IF_DEFAULT and self._is_default_template_content(content, filename):
                    self._bootstrap_cache[filename] = (mtime, None)  # sentinel: skipped
                    continue
                self._bootstrap_cache[filename] = (mtime, content)
                cached = (mtime, content)
            else:
                if cached[1] is None:
                    continue  # cached as skipped, still default

            parts.append(f"## {filename}\n\n{self._shift_headings(cached[1], offset=1)}")

        return "\n\n".join(parts) if parts else ""

    def _load_lessons(self) -> str:
        """Load past lessons from tasks/lessons.md into the system prompt."""
        lessons_path = self.workspace / "tasks" / "lessons.md"
        if not lessons_path.exists():
            return ""
        try:
            content = lessons_path.read_text(encoding="utf-8").strip()
            if not content:
                return ""
            return f"## Past Lessons\n\n{self._shift_headings(content, offset=1)}"
        except Exception as e:
            logger.warning("Failed to load lessons: {}", e)
            return ""

    @staticmethod
    def _is_default_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if not tpl.is_file():
                return False
            mtime = tpl.stat().st_mtime
            cached = _template_content_cache.get(template_path)
            if cached is None or cached[0] != mtime:
                if len(_template_content_cache) >= _MAX_TEMPLATE_CACHE_SIZE:
                    _template_content_cache.clear()
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
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        cs = context_state or ContextState()
        sys_static = self.build_system_prompt(
            channel=channel,
            tool_definitions=cs.tool_definitions,
        )
        retained_history = history

        # Dynamic system content — runtime metadata, memory, state
        sys_dynamic_parts: list[str] = []

        runtime_lines = [f"Current Time: {current_time_str(self.timezone)}"]
        if channel:
            runtime_lines.append(f"Channel: {channel}")
        if cs.current_iteration is not None and cs.max_iterations is not None:
            runtime_lines.append(f"Iteration: {cs.current_iteration}/{cs.max_iterations}")
        sys_dynamic_parts.append("\n".join(runtime_lines))

        memory_section = self._build_memory_section()
        if memory_section:
            sys_dynamic_parts.append(memory_section)

        state_block = self._build_task_tree_section()
        if state_block:
            sys_dynamic_parts.append(f"# Current State — what to focus on and what has happened\n\n{state_block}")

        if sys_dynamic_parts:
            sys_static = sys_static + "\n\n# Runtime Context\n\n" + "\n\n".join(sys_dynamic_parts)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": sys_static},
        ]

        messages.extend(retained_history)

        # Clean user message — no injected metadata
        user_content = self._build_user_content(current_message, media)
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), user_content)
            messages[-1] = last
        else:
            messages.append({"role": current_role, "content": user_content})

        # Inject Message Time into non-last tool/user messages
        header = format_message_header()
        for i in range(len(messages) - 1):
            role = messages[i].get("role")
            if role not in ("tool", "user"):
                continue
            content = messages[i].get("content", "")
            if not content:
                continue
            if isinstance(content, str):
                messages[i]["content"] = f"{header}\n{content}"
            elif isinstance(content, list):
                messages[i]["content"] = [{"type": "text", "text": header}] + list(content)

        return self._backfill_thinking_to_content(messages)

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
        reasoning_details: list[dict] | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            reasoning_details=reasoning_details,
            thinking_blocks=thinking_blocks,
        ))
        return messages
