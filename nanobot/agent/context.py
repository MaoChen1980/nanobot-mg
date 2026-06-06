"""Context builder for assembling agent prompts."""

from __future__ import annotations

import base64
import mimetypes
import os
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
from nanobot.utils.helpers import split_thinking_messages as _split_thinking_messages
from nanobot.utils.media_decode import compress_image, detect_image_mime, image_placeholder_text
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.tools_index import rebuild_tools_index as _rebuild_tools_index

# Module-level cache for template file contents (path -> (mtime, content))
_template_content_cache: dict[str, tuple[float, str]] = {}
_MAX_TEMPLATE_CACHE_SIZE = 20

# Regex matching Windows absolute paths with backslashes (e.g. C:\Users\foo)
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'|&;<>()$`]*")


def normalize_paths(text: str) -> str:
    """Convert Windows backslash paths to forward slashes so the LLM doesn't
    misread ``\\u`` / ``\\n`` as escape sequences.
    """
    return _WIN_PATH_RE.sub(lambda m: m.group(0).replace("\\", "/"), text)


@dataclass
class ContextState:
    """Runtime state for LLM context assembly.

    Carries session-level parameters that change slowly across turns,
    extracted from ``build_messages()`` to reduce per-call boilerplate.
    """
    tool_definitions: list[dict[str, Any]] | None = None
    current_iteration: int | None = None
    max_iterations: int | None = None
    context_window_tokens: int | None = None
    history_budget_tokens: int | None = None


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "TOOLS.md"]
    _SKIP_IF_DEFAULT = {"USER.md"}  # TOOLS.md is auto-generated — always inject
    _RUNTIME_CONTEXT_TAG = "## Runtime Context"
    _RUNTIME_CONTEXT_END = "## /Runtime Context"

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None, db=None,
                 project_root: Path | None = None, framework_config: dict[str, int] | None = None):
        self.workspace = workspace
        self.project_root = project_root
        self.timezone = timezone
        self.memory = MemoryStore(workspace, db=db)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        self._bootstrap_cache: dict[str, tuple[float, str | None]] = {}
        self._framework_config = framework_config or {}


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
        runtime_context: str | None = None,
    ) -> str:
        """Build the static portion of the system prompt (identity, tools, bootstrap, skills)."""
        _t0 = time.time()
        identity = self._get_identity(channel=channel)

        tools = None
        if tool_definitions:
            tools = self._build_tools_section(tool_definitions)

        # Regenerate tools index so TOOLS.md is always fresh
        _rebuild_tools_index(self.workspace)

        bootstrap = self._load_bootstrap_files()

        workflow_routing = self._build_workflow_routing()

        always_content = None
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.format_skills_for_context(always_skills)

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            skills_section = render_template("agent/skills_section.md", skills_summary=skills_summary)
        else:
            skills_section = None

        result = render_template(
            "agent/system_prompt.md",
            identity=identity,
            tools=tools,
            bootstrap=bootstrap or None,
            workflows=workflow_routing or None,
            always_skills=always_content,
            skills_summary=skills_section,
            runtime_context=runtime_context,
            # Framework config — used by framework_core.md via {% include %}
            max_iterations=self._framework_config.get("max_iterations", 200),
            context_window_tokens=self._framework_config.get("context_window_tokens", 200_000),
            max_tool_result_chars=self._framework_config.get("max_tool_result_chars", 32_000),
            exec_timeout=self._framework_config.get("exec_timeout", 60),
            subagent_max_iterations=self._framework_config.get("subagent_max_iterations", 100),
            heartbeat_interval_minutes=self._framework_config.get("heartbeat_interval_minutes", 30),
        )

        _elapsed = (time.time() - _t0) * 1000
        if _elapsed > 100:
            logger.info("build_system_prompt took {:.0f}ms", _elapsed)
        return normalize_paths(result)

    def _build_tools_section(self, tool_definitions: list[dict[str, Any]]) -> str:
        """Build the available tools section for the system prompt."""
        if not tool_definitions:
            return ""
        lines = [
            "# Available Tools\n",
            "注意：只读工具（exec_tool/read_file_tool/glob_tool 等）相同参数 60s 内重复调用返回缓存结果。"
            "任何工具连续返回相同内容会被去重为简短提示。\n",
        ]
        for schema in tool_definitions:
            fn = schema.get("function", {})
            name = fn.get("name", "unknown")
            desc = fn.get("description", "")
            # Truncate at 10K to prevent MCP mega-descriptions from
            # dominating the prompt.  Keeps \n\n section boundaries intact.
            if len(desc) > 10_000:
                truncated = desc[:9_997]
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
        lines.append(
            "**⚠️ 不要在 content 中写工具名**：上面的工具名（`exec_tool`、`read_file_tool` 等）仅用于 tool_call API。"
            "如果需要在文本中提及工具操作，用自然语言描述（如'执行命令''读取文件'），不要写出工具名字符串。"
            "框架会自动检测 content 中的工具名并触发重试。\n"
        )
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

    def _get_identity(self, channel: str | None = None, include_vector_search: bool = True) -> str:
        """Get the core identity section."""
        workspace_path = self.workspace.expanduser().resolve().as_posix()
        from nanobot.config.paths import get_data_dir
        import shutil
        data_dir = get_data_dir().as_posix()
        system = platform.system()

        os_platform = "macOS" if system == "Darwin" else ("Windows" if system == "Windows" else "Linux")
        arch = platform.machine()
        python_version = platform.python_version()

        # System resources — read once at identity build time
        cpu_cores = os.cpu_count() or 1
        try:
            du = shutil.disk_usage(workspace_path)
            disk_free_str = _fmt_gb(du.free)
        except Exception:
            disk_free_str = "unknown"
        memory_total_str, memory_avail_str = _get_memory_info()
        gpu_str = _get_gpu_info()

        kwargs: dict[str, object] = dict(
            workspace_path=workspace_path,
            data_dir=data_dir,
            os_platform=os_platform,
            os_version=platform.release(),
            arch=arch,
            python_version=python_version,
            channel=channel,
            model=self._framework_config.get("model"),
            provider=self._framework_config.get("provider"),
            timezone=self.timezone,
            context_window_tokens=self._framework_config.get("context_window_tokens", 200_000),
            max_iterations=self._framework_config.get("max_iterations", 200),
            max_tool_result_chars=self._framework_config.get("max_tool_result_chars", 32_000),
            exec_timeout=self._framework_config.get("exec_timeout", 60),
            subagent_max_iterations=self._framework_config.get("subagent_max_iterations", 100),
            heartbeat_interval_minutes=self._framework_config.get("heartbeat_interval_minutes", 30),
            cpu_cores=cpu_cores,
            memory_total=memory_total_str,
            memory_available=memory_avail_str,
            disk_free=disk_free_str,
            gpu=gpu_str,
        )

        if include_vector_search:
            try:
                import sentence_transformers  # noqa: F401
                kwargs["sentence_transformers"] = True
            except ImportError:
                kwargs["sentence_transformers"] = False
        else:
            kwargs["sentence_transformers"] = None

        return render_template("agent/identity.md", **kwargs)

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
    def _split_thinking_messages(messages: list[dict]) -> list[dict]:
        """Split assistant messages with thinking/reasoning into separate messages.
        Delegates to nanobot.utils.helpers.split_thinking_messages."""
        return _split_thinking_messages(messages)

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
            "# Task Tree - workspace/tasks/TREE.md\n\n"
            "Current task tree. Tasks are managed as files under workspace/tasks/ — "
            "use read_file_tool/write_file_tool/edit_file_tool to update them.\n\n"
            + self._shift_headings(content, offset=1)
        )

    def _build_current_context_section(self) -> str:
        """Read tasks/CURRENT.md from the workspace for session-level working context."""
        current_path = self.workspace / "tasks" / "CURRENT.md"
        if not current_path.exists():
            return ""
        try:
            content = current_path.read_text(encoding="utf-8").strip()
        except Exception:
            logger.warning("Failed to read current context at {}", current_path)
            return ""
        if not content:
            return ""
        return (
            "# Working Context - workspace/tasks/CURRENT.md\n\n"
            "Session-level working context. Tracks what you're doing this session. "
            "Create and update it with write_file_tool.\n\n"
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
                parts.append(f"# Memory - workspace/memory/MEMORY.md\n\n{index_text}")

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

    @staticmethod
    def _strip_old_images(messages: list[dict[str, Any]]) -> None:
        """Replace base64 media in ALL history messages with text placeholders.

        Mutates *messages* in-place.  Only the incoming user message (appended
        *after* this call) keeps its image_url blocks — everything in history
        gets cleaned so old base64 never wastes context budget on subsequent
        turns.

        Handles both list content (``image_url`` / ``image`` / ``input_*``
        blocks in user messages) and string content (``data:...;base64,``
        embedded in tool results).  ``maybe_persist_tool_result`` skips mixed
        ``[image_url, text]`` lists, so the runner embeds full base64 directly
        in tool message text — this is where the string-case cleanup applies.
        """
        _BASE64_DATA_RE = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]{100,}")

        for msg in messages:
            content = msg.get("content")
            role = msg.get("role")

            # ---- list content: find and replace media blocks ----
            if isinstance(content, list):
                cleaned: list[dict[str, Any]] | None = None
                path_from_meta = ""

                for b in content:
                    if not isinstance(b, dict):
                        if cleaned is not None:
                            cleaned.append(b)  # type: ignore[arg-type]
                        continue

                    bt = b.get("type", "")

                    # Detect media blocks: image_url, image, input_image, etc.
                    is_media = (
                        "image" in bt
                        or bt.startswith("input_")
                        or (
                            isinstance(b.get("source"), dict)
                            and b["source"].get("type") == "base64"
                        )
                    )

                    if is_media:
                        cleaned = cleaned if cleaned is not None else list(content[: content.index(b)])
                        path_from_meta = (b.get("_meta") or {}).get("path", "") or path_from_meta
                    elif cleaned is not None:
                        cleaned.append(b)

                if cleaned is not None:
                    if not cleaned:
                        placeholder = image_placeholder_text(path_from_meta or "", empty="[image]")
                        cleaned = [{"type": "text", "text": placeholder}]
                    msg["content"] = cleaned
                continue

            # ---- string content: strip embedded base64 (tool results) ----
            if isinstance(content, str) and _BASE64_DATA_RE.search(content):
                msg["content"] = _BASE64_DATA_RE.sub("[base64 data omitted]", content)

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
                        name = filename.replace(".md", "").title()
                        if filename == "TOOLS.md":
                            parts.append(f"# {name} - workspace/{filename}\n\n{content}")
                        else:
                            parts.append(f"# {name} - workspace/{filename}\n\n{self._shift_headings(content, offset=1)}")
                except Exception as e:
                    logger.warning("Failed to load bundled template {}: {}", filename, e)
                continue

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue

            cached = self._bootstrap_cache.get(filename)
            if cached is not None and cached[0] == mtime:
                if cached[1] is None:
                    continue  # cached as skipped, still default
                content_str: str = cached[1]
            else:
                content = file_path.read_text(encoding="utf-8")
                # Skip if user hasn't customized this file (still default template)
                if filename in self._SKIP_IF_DEFAULT and self._is_default_template_content(content, filename):
                    self._bootstrap_cache[filename] = (mtime, None)  # sentinel: skipped
                    continue
                self._bootstrap_cache[filename] = (mtime, content)
                content_str = content

            name = filename.replace(".md", "").title()
            if filename == "TOOLS.md":
                parts.append(f"# {name} - workspace/{filename}\n\n{content_str}")
            else:
                parts.append(f"# {name} - workspace/{filename}\n\n{self._shift_headings(content_str, offset=1)}")

        return "\n\n".join(parts) if parts else ""

    def _build_workflow_routing(self) -> str:
        """Build workflow routing table from workspace/framework/workflows/ so
        workflows are discoverable via framework_search_tool."""
        wf_dir = self.workspace / "framework" / "workflows"
        if not wf_dir.is_dir():
            return ""
        lines: list[str] = []
        for f in sorted(wf_dir.iterdir()):
            if not f.name.endswith(".md"):
                continue
            try:
                c = f.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not c:
                continue
            trigger = ""
            for line in c.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    trigger = line[:120]
                    break
            lines.append(f"- **{f.stem}**: {trigger} — `framework_search_tool(query=\"{f.stem}\")`")
        if not lines:
            return ""
        return "## Workflows\n\nSearch with framework_search_tool when scenario matches:\n" + "\n".join(lines)

    @staticmethod
    def _is_default_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if not tpl.is_file():
                return False
            mtime = tpl.stat().st_mtime  # type: ignore[attr-defined]
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
        # Runtime metadata — injected into system prompt (template puts it at the end)
        runtime_lines = [f"Current Time: {current_time_str(self.timezone)}"]
        if channel:
            runtime_lines.append(f"Channel: {channel}")
        if cs.context_window_tokens is not None:
            runtime_lines.append(f"Context Window: {cs.context_window_tokens} tokens")
        if cs.history_budget_tokens is not None:
            runtime_lines.append(f"History Budget: ~{cs.history_budget_tokens} tokens available")

        sys_static = self.build_system_prompt(
            channel=channel,
            tool_definitions=cs.tool_definitions,
            runtime_context="\n".join(runtime_lines),
        )
        retained_history = history

        # Dynamic session state — appended to system prompt (changes each turn)
        session_parts: list[str] = []

        memory_section = self._build_memory_section()
        if memory_section:
            session_parts.append(memory_section)

        state_block = self._build_task_tree_section()
        if state_block:
            session_parts.append(f"# Current State — what to focus on and what has happened\n\n{state_block}")

        current_block = self._build_current_context_section()
        if current_block:
            session_parts.append(current_block)

        if session_parts:
            sys_static = sys_static + "\n\n" + "\n\n".join(session_parts)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": sys_static},
        ]

        messages.extend(retained_history)

        # Strip image data from history — only the incoming user message keeps
        # its image_url blocks.  Old images and base64-laden tool results are
        # replaced with text placeholders to prevent token bloat across turns.
        self._strip_old_images(messages)

        # Clean user message — no injected metadata
        user_content = self._build_user_content(current_message, media)
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), user_content)
            messages[-1] = last
        else:
            messages.append({"role": current_role, "content": user_content})

        # Inject Message Time into non-last user messages
        header = format_message_header()
        for i in range(len(messages) - 1):
            role = messages[i].get("role")
            if role != "user":
                continue
            content = messages[i].get("content", "")
            if not content:
                continue
            if isinstance(content, str):
                # Skip if already stamped (e.g. re-injected message)
                if content.startswith("====== Message Time:"):
                    continue
                messages[i]["content"] = f"{header}\n{content}"
            elif isinstance(content, list):
                messages[i]["content"] = [{"type": "text", "text": header}] + list(content)

        return self._split_thinking_messages(messages)

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
            compressed, out_mime = compress_image(raw, mime, max_bytes=200 * 1024)
            b64 = base64.b64encode(compressed).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{out_mime};base64,{b64}"},
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


# -- resource introspection helpers (used by _get_identity) -------------------------


def _fmt_gb(n: float | int | None) -> str:
    """Format bytes to GB string."""
    if n is None:
        return "unknown"
    gb = n / (1024 ** 3)
    return f"{gb:.1f} GB"


def _get_memory_info() -> tuple[str, str]:
    """Return (total_memory_str, available_memory_str), best-effort."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return _fmt_gb(mem.total), _fmt_gb(mem.available)
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: platform-specific commands (Windows) / pure Python (macOS, Linux)
    import sys
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty TotalVisibleMemorySize,FreePhysicalMemory"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    total_kb = int(parts[0])
                    free_kb = int(parts[1])
                    return _fmt_gb(total_kb * 1024), _fmt_gb(free_kb * 1024)
        elif sys.platform == "darwin":
            import os as _os
            total = _os.sysconf('SC_PHYS_PAGES') * _os.sysconf('SC_PAGE_SIZE')
            return _fmt_gb(total), "unknown"
        elif sys.platform == "linux":
            with open("/proc/meminfo") as _f:
                total_kb = avail_kb = 0
                for _line in _f:
                    if _line.startswith("MemTotal:"):
                        total_kb = int(_line.split()[1])
                    elif _line.startswith("MemAvailable:"):
                        avail_kb = int(_line.split()[1])
            if total_kb:
                return _fmt_gb(total_kb * 1024), _fmt_gb(avail_kb * 1024)
    except Exception:
        pass
    return "unknown", "unknown"


def _get_gpu_info() -> str | None:
    """Return GPU description string, or None if no GPU detected."""
    try:
        import torch
        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            names: list[str] = []
            for i in range(count):
                names.append(torch.cuda.get_device_name(i))
            return ", ".join(names)
    except Exception:
        pass
    import subprocess
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True, timeout=10
        )
        gpus = [line.strip() for line in out.strip().splitlines() if line.strip()]
        if gpus:
            return "; ".join(gpus)
    except Exception:
        pass
    return None
