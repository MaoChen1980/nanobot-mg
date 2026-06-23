"""Context builder for assembling agent prompts."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import platform
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, current_time_str
from nanobot.utils.helpers import split_thinking_messages as _split_thinking_messages
from nanobot.utils.media_decode import compress_image, detect_image_mime, image_placeholder_text
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.tools_index import rebuild_tools_index as _rebuild_tools_index

# Module-level cache for template file contents (path -> (mtime, content))
_template_content_cache: dict[str, tuple[float, str]] = {}
_MAX_TEMPLATE_CACHE_SIZE = 20

# Module-level caches for system info — computed once per session
_memory_info_cache: tuple[str, str] | None = None  # (total, available)
_gpu_info_cache: str | None = None  # GPU description

# Regex matching Windows absolute paths with backslashes (e.g. C:\Users\foo)
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'|&;<>()$`]*")
_SESSION_KEY_RE = re.compile(r"[^\w.-]")
# Base64 data URL detection — used in _strip_old_images (called every turn)
_base64_DATA_RE = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]{100,}")


def _sanitize_session_key(key: str) -> str:
    """Replace characters unsafe for filenames (e.g. ``:`` in ``cli:direct``).

    Applies a broader character filter than ``safe_filename()`` (replaces
    anything outside ``[\\w.-]``) and strips leading/trailing whitespace
    first so the suffix is always clean.
    """
    return _SESSION_KEY_RE.sub("_", key.strip())


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
        self._workspace_path_str = workspace.expanduser().resolve().as_posix()
        self.memory = MemoryStore(workspace, db=db)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        self._bootstrap_cache: dict[str, tuple[float, str | None]] = {}
        self._framework_config = framework_config or {}
        # Content caches — invalidated by mtime change
        self._file_text_cache: dict[str, tuple[float, str]] = {}
        self._identity_cache: dict[tuple, str] = {}
        # Batched .memory_usage.json writes: accumulate, flush every 10 turns
        self._pending_memory_entries: list[dict] = []
        # Cache for _build_memory_quality_note: (mtime, formatted_string)
        self._memory_quality_cache: tuple[float, str] | None = None


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

    def _cached_read_text(self, path: Path) -> str | None:
        """Read file text with mtime-based caching.

        Returns None if the file doesn't exist or can't be read.
        Cache is invalidated when the file's mtime changes.
        """
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        key = str(path)
        cached = self._file_text_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None
        self._file_text_cache[key] = (mtime, content)
        return content

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        runtime_context: str | None = None,
        session_key: str | None = None,
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

        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        tree_rel = f"tasks/tree{suffix}.json"
        current_rel = f"tasks/CURRENT{suffix}.md"
        team_board_rel = f"tasks/team_board{suffix}.md"

        result = render_template(
            "agent/system_prompt.md",
            identity=identity,
            tools=tools,
            bootstrap=bootstrap or None,
            runtime_context=runtime_context,
            # Workspace path — used by included templates (framework_core etc.)
            workspace_path=self._workspace_path_str,
            # Session-scoped paths (full and relative)
            tree_path=f"{self._workspace_path_str}/{tree_rel}",
            current_path=f"{self._workspace_path_str}/{current_rel}",
            team_board_path=f"{self._workspace_path_str}/{team_board_rel}",
            tree_rel=tree_rel,
            current_rel=current_rel,
            team_board_rel=team_board_rel,
            # Framework config — used by framework_core.md via {% include %}
            max_iterations=self._framework_config.get("max_iterations", 200),
            context_window_tokens=self._framework_config.get("context_window_tokens", 200_000),
            max_tool_result_chars=self._framework_config.get("max_tool_result_chars", 32_000),
            exec_timeout=self._framework_config.get("exec_timeout", 60),
            subagent_max_iterations=self._framework_config.get("subagent_max_iterations", 100),
            heartbeat_interval_minutes=self._framework_config.get("heartbeat_interval_minutes", 30),
        )

        # Post-process: replace old path references in included templates
        if suffix:
            old_tree = f"{self._workspace_path_str}/tasks/tree.json"
            old_current = f"{self._workspace_path_str}/tasks/CURRENT.md"
            old_board = f"{self._workspace_path_str}/tasks/team_board.md"
            result = result.replace(old_tree, f"{self._workspace_path_str}/{tree_rel}")
            result = result.replace(old_current, f"{self._workspace_path_str}/{current_rel}")
            result = result.replace(old_board, f"{self._workspace_path_str}/{team_board_rel}")

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
            # Indent continuation lines for LLM-readable hierarchy (list+join avoids O(n²))
            _desc_lines = desc.split("\n")
            _parts = [_desc_lines[0]]
            for _line in _desc_lines[1:]:
                _parts.append("\n  " + _line if _line.strip() else "\n")
            indented_desc = "".join(_parts)
            lines.append(f"- **{name}**: {indented_desc}")
            lines.append("")  # blank line between tools
        lines.append(
            "**⚠️ 不要在 content 中写工具名**：上面的工具名（`exec_tool`、`read_file_tool` 等）仅用于 tool_call API。"
            "如果需要在文本中提及工具操作，用自然语言描述（如'执行命令''读取文件'），不要写出工具名字符串。"
            "框架会自动检测 content 中的工具名并触发重试。\n"
        )
        return "\n".join(lines)

    def build_instructions_section(self, *, for_subagent: bool = False, session_key: str | None = None) -> str:
        """Build instructions block (prepended to last user message, near generation point).

        These are directive/procedural rules that LLMs treat as instructions
        when placed immediately before the user message, rather than as
        reference material in the system prompt.

        When *for_subagent* is True, uses subagent-specific snippets and
        skips orchestrator-only content (orchestration_guide).
        """
        sections: list[str] = []

        # Rules from RULES.md — extracted instruction-type findings
        rules_text = self.memory.read_rules().strip()
        if rules_text:
            sections.append(f"## Rules\n\n{rules_text}")

        # Session-scoped file paths for instructions that reference them
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        tree_rel = f"tasks/tree{suffix}.json"
        current_rel = f"tasks/CURRENT{suffix}.md"
        team_board_rel = f"tasks/team_board{suffix}.md"

        # Static instruction snippets loaded from template files
        # Edit these files to change instruction content (no Python changes needed)
        if for_subagent:
            snippet_names = [
                "external_content_safety",
                "output_rules_subagent",
                "think_triggers",
                "search_tool_selector",
                "operating_principles_subagent",
                "subagent_escalation",
                "meta_learning",
                "skill_refinement",
                "tool_result_summary",
            ]
        else:
            snippet_names = [
                "external_content_safety",
                "output_rules",
                "think_triggers",
                "search_tool_selector",
                "operating_principles",
                "orchestration_guide",
                "task_tree",
                "meta_learning",
                "skill_refinement",
                "tool_result_summary",
            ]
        template_kwargs = dict(
            workspace_path=self._workspace_path_str,
            tree_path=f"{self._workspace_path_str}/{tree_rel}",
            current_path=f"{self._workspace_path_str}/{current_rel}",
            team_board_path=f"{self._workspace_path_str}/{team_board_rel}",
            tree_rel=tree_rel,
            current_rel=current_rel,
            team_board_rel=team_board_rel,
        )
        for name in snippet_names:
            content = render_template(
                f"agent/_instructions/{name}.md",
                **template_kwargs,
            )
            if content.strip():
                sections.append(content)

        # Post-process: replace well-known path references with session-scoped equivalents.
        # This covers templates that still use `{{ workspace_path }}/tasks/tree.json` etc.
        # without requiring every template file to be updated.
        if suffix:
            old_tree = f"{self._workspace_path_str}/tasks/tree.json"
            old_current = f"{self._workspace_path_str}/tasks/CURRENT.md"
            old_board = f"{self._workspace_path_str}/tasks/team_board.md"
            for i, sec in enumerate(sections):
                sec = sec.replace(old_tree, f"{self._workspace_path_str}/{tree_rel}")
                sec = sec.replace(old_current, f"{self._workspace_path_str}/{current_rel}")
                if team_board_rel:
                    sec = sec.replace(old_board, f"{self._workspace_path_str}/{team_board_rel}")
                sections[i] = sec

        # Always-skills — full content, always injected near generation point
        always_skills_names = self.skills.get_always_skills()
        if always_skills_names:
            always_content = self.skills.format_skills_for_context(always_skills_names)
            if always_content:
                sections.append(
                    "## Active Skills\n\n"
                    "> **Always 审查**：以下 skill 标记为 always=true，每轮自动注入。\n"
                    "> 定期评估每个 always-skill：如果其行为已成为你的自然倾向（不注入也能正确执行），\n"
                    "> 用 edit_file_tool 将其 frontmatter 的 `always: true` 改为 `false`，减少上下文开销。\n\n"
                    f"{always_content}"
                )

        # Available skills summary — dynamically built, excludes always-skills
        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills_names))
        if skills_summary:
            sections.append(
                "### Available Skills\n\n"
                "以下 skills 扩展了你的能力。当用户输入匹配某个 skill 的描述时，"
                "必须优先加载该 skill——用 read_file_tool 阅读其 SKILL.md 并按步骤执行。\n\n"
                f"{skills_summary}"
            )

        # Current task tree — injected as verification standard (not reference)
        # Re-injected every iteration via instructions, immune to compression.
        if not for_subagent:
            task_tree = self._build_task_tree_section(session_key=session_key)
            current_ctx = self._build_current_context_section(session_key=session_key)
            task_parts: list[str] = []
            if task_tree:
                task_parts.append(task_tree)
            if current_ctx:
                task_parts.append(current_ctx)
            if task_parts:
                sections.append(
                    "## 当前任务与验收标准\n\n"
                    "以下是你当前的任务目标和进度，请用它作为最终验证标准来检查自己的工作。\n"
                    "**每次完成一个步骤后，检查它是否真正推进了根任务目标。**\n"
                    "如果你发现自己在做与根任务无关的事情，停下来重新规划。\n\n"
                    + "\n\n".join(task_parts)
                )

        # Team board — auto-injected for both Orchestrator and Subagents
        team_board = self._build_team_board_section(session_key=session_key)
        if team_board:
            heading = (
                "## Team Board — 当前项目事实黑板\n\n"
                "以下是当前项目所有节点共享的事实发现。这些信息已自动注入上下文，"
                "无需额外 read_file_tool。\n\n"
                if for_subagent
                else "## Team Board\n\n"
            )
            sections.append(heading + team_board)

        return "\n\n".join(sections)

    def _build_team_board_section(self, session_key: str | None = None) -> str:
        """Read tasks/team_board*.md from the workspace and return content if non-empty."""
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        board_path = self.workspace / "tasks" / f"team_board{suffix}.md"
        content = self._cached_read_text(board_path)
        if not content:
            return ""
        content = content.strip()
        if not content:
            return ""
        # Cap at 4000 chars to prevent unbounded context consumption
        _MAX_BOARD_CHARS = 4000
        if len(content) > _MAX_BOARD_CHARS:
            truncation_note = (
                f"\n\n> ⚠️ Board truncated ({len(content)} chars > {_MAX_BOARD_CHARS} limit). "
                "Use `read_file_tool` to see full content."
            )
            content = content[:_MAX_BOARD_CHARS] + truncation_note
        return self._shift_headings(content, offset=1)

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
        """Get the core identity section. Cached by (channel, include_vector_search)."""
        cache_key = (channel, include_vector_search)
        cached = self._identity_cache.get(cache_key)
        if cached is not None:
            return cached

        workspace_path = self._workspace_path_str
        import shutil

        from nanobot.config.paths import get_data_dir
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

        result = render_template("agent/identity.md", **kwargs)
        self._identity_cache[cache_key] = result
        return result

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

    def _has_active_tasks(self, items: list[dict], _depth: int = 0) -> bool:
        """Check if tree.json has any non-terminal tasks (active, pending, etc.).

        Missing/null status is treated as active, consistent with
        ``_render_tree_items`` which renders it as ``○``.
        A depth guard prevents infinite recursion from malformed cycles.
        """
        if _depth > 100:
            logger.warning("_has_active_tasks: max depth exceeded — possible cycle in tree.json")
            return True
        for item in items:
            status = item.get("status")
            if status is None or status not in ("completed", "failed"):
                return True
            if self._has_active_tasks(
                    self._get_children(items, item.get("id", "")),
                    _depth=_depth + 1):
                return True
        return False

    @staticmethod
    def _get_children(items: list[dict], parent_id: str) -> list[dict]:
        return [it for it in items if it.get("parent") == parent_id]

    def _build_task_tree_section(self, session_key: str | None = None) -> str:
        """Read tasks/tree*.json from the workspace and render as a tree.

        Per-session isolation via session_key (e.g. ``tree_cli_direct.json``).
        Only injects when there are active (non-completed, non-failed) tasks.
        """
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        tree_path = self.workspace / "tasks" / f"tree{suffix}.json"
        tree_rel = f"tasks/tree{suffix}.json"
        raw = self._cached_read_text(tree_path)
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse %s — skipping tree section", tree_rel)
            return ""
        items = data.get("items", [])
        if not items:
            return ""
        # Skip injection when no active tasks remain
        if not self._has_active_tasks(items):
            logger.debug("All tasks completed — skipping tree injection")
            return ""
        rendered = self._render_tree_items(items, parent=None, depth=0)
        return (
            f"# Task Tree - {self._workspace_path_str}/{tree_rel}\n\n"
            f"Current task tree. Tree data is in {tree_rel} — "
            "use read_file_tool/write_file_tool/edit_file_tool to update it. "
            "Schema reference: tasks/tree.schema.md.\n\n"
            + rendered
        )

    @staticmethod
    def _render_tree_items(items: list[dict], parent: str | None = None, depth: int = 0) -> str:
        """Render tree.json items as indented text, recursing from roots (parent=None)."""
        STATUS_MARKS = {
            "completed": "✅", "active": "○", "pending": "·",
            "failed": "✗", "paused": "⏸",
        }
        children = [it for it in items if it.get("parent") == parent]
        if not children:
            return ""
        lines: list[str] = []
        # Stable sort: roots first, then by created date within same depth
        children.sort(key=lambda it: (
            0 if it.get("parent") is None else 1,
            it.get("created", ""),
            it.get("id", ""),
        ))
        for child in children:
            cid = child.get("id", "")
            name = child.get("name", cid)
            status = child.get("status", "active")
            mark = STATUS_MARKS.get(status, "○")
            indent = "  " * depth
            doc = child.get("doc", "")
            if doc:
                lines.append(f"{indent}- {mark} **{name}** [{status}] → {doc}")
            else:
                lines.append(f"{indent}- {mark} **{name}** [{status}]")
            note = child.get("note", "")
            if note:
                lines.append(f"{indent}  └ note: {note}")
            lines.append(ContextBuilder._render_tree_items(items, parent=cid, depth=depth + 1))
        return "\n".join(lines)

    def _build_current_context_section(self, session_key: str | None = None) -> str:
        """Read tasks/CURRENT*.md from the workspace (per-session isolation via session_key)."""
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        current_path = self.workspace / "tasks" / f"CURRENT{suffix}.md"
        current_rel = f"tasks/CURRENT{suffix}.md"
        content = self._cached_read_text(current_path)
        if not content:
            return ""
        content = content.strip()
        if not content:
            return ""
        return (
            f"# Working Context - {self._workspace_path_str}/{current_rel}\n\n"
            "Project-level working context. Tracks the current project node's progress. "
            "Create and update it with write_file_tool.\n\n"
            + self._shift_headings(content, offset=1)
        )

    def _build_self_findings_section(self) -> str:
        """Read workspace/framework/self_findings.md for system prompt injection (cached by mtime).

        Written by SelfDetectHook after each detection cycle (~15 turns).
        Renders at the end of system content via session_parts, alongside other
        dynamic context like memory and task-tree.
        """
        path = self.workspace / "framework" / "self_findings.md"
        content = self._cached_read_text(path)
        if not content:
            return ""
        return content.strip()

    def _build_user_feedback_section(self) -> str:
        """Read workspace/framework/user_feedback.md for system prompt injection.

        Written by MemoryExtractor._process_user_feedback() — aggregates
        user correction/rejection signals detected by SelfDetectHook into
        structured patterns the agent can learn from.
        """
        path = self.workspace / "framework" / "user_feedback.md"
        content = self._cached_read_text(path)
        if not content:
            return ""
        return content.strip()

    # -- vector-indexed memory -------------------------------------------------

    def _build_memory_section(self) -> str:
        """Build memory section: MEMORY.md + key content files (no vector search)."""
        memory_dir = self.memory.memory_dir
        parts = []

        # Load MEMORY.md index (cached by mtime)
        index_content = self._cached_read_text(self.memory.memory_file) or ""
        if index_content and not self._is_default_template_content(index_content, "memory/MEMORY.md"):
            lines = index_content.split("\n")
            if lines and lines[0].startswith("# "):
                lines = lines[1:]
            index_text = "\n".join(lines).strip()
            if index_text:
                parts.append(f"# Memory - {self._workspace_path_str}/memory/MEMORY.md\n\n{index_text}")

        # Also inline key memory files so rules/preferences are visible without recall (cached by mtime)
        MAX_MEMORY_CHARS = 2000
        for name in ("system.md", "user.md"):
            fpath = memory_dir / name
            text = self._cached_read_text(fpath)
            if text:
                text = text.strip()
                if len(text) > MAX_MEMORY_CHARS:
                    text = text[:MAX_MEMORY_CHARS] + "\n\n... (truncated, see file in memory/)"
                heading = name.replace(".md", "").title()
                parts.append(f"### {heading}\n\n{text}")

        # Track injection for quality stats
        self._track_memory_injection(parts)

        memory_quality = self._build_memory_quality_note()
        if memory_quality:
            parts.append(memory_quality)

        if not parts:
            return ""
        return (
            "# Memory\n\n"
            "## Long-term Memory\n\n"
            "This is your persistent memory — facts, conventions, and patterns "
            "learned from past work. Follow these guidelines in your responses.\n\n"
            + "\n\n".join(parts)
        )

    def _track_memory_injection(self, parts: list[str]) -> None:
        """Log which memory files are being injected (for quality tracking).

        Accumulates in memory and flushes to disk every 10 turns
        to reduce per-turn I/O overhead.
        """
        self._pending_memory_entries.append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files_injected": len(parts),
        })
        if len(self._pending_memory_entries) >= 10:
            self._flush_memory_entries()

    def _flush_memory_entries(self) -> None:
        """Flush pending memory usage entries to disk."""
        if not self._pending_memory_entries:
            return
        path = self.workspace / "framework" / ".memory_usage.json"
        try:
            old: list[dict] = json.loads(self._cached_read_text(path) or "[]")
        except (json.JSONDecodeError, OSError):
            old = []
        old.extend(self._pending_memory_entries)
        self._pending_memory_entries.clear()
        if len(old) > 100:
            old = old[-100:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    def _build_memory_quality_note(self) -> str:
        """Return a note about memory quality from injection history (cached by mtime)."""
        path = self.workspace / "framework" / ".memory_usage.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return ""
        if self._memory_quality_cache is not None and self._memory_quality_cache[0] == mtime:
            return self._memory_quality_cache[1]
        try:
            data: list[dict] = json.loads(self._cached_read_text(path) or "[]")
        except (json.JSONDecodeError, OSError):
            return ""
        if len(data) < 3:
            self._memory_quality_cache = (mtime, "")
            return ""
        recent = data[-20:]
        total = sum(e.get("files_injected", 0) for e in recent)
        avg = total / len(recent)
        note = (
            f"#### Memory Usage Note\n\n"
            f"Memory injected {avg:.0f} section(s) per turn on average "
            f"(last {len(recent)} injections). "
            f"If you notice outdated or irrelevant memories, use the memory tools "
            f"to update or remove them.\n"
        )
        self._memory_quality_cache = (mtime, note)
        return note

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
        _base64_data_re = _base64_DATA_RE

        for msg in messages:
            content = msg.get("content")

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
            if isinstance(content, str) and _base64_data_re.search(content):
                msg["content"] = _base64_data_re.sub("[base64 data omitted]", content)

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
                            parts.append(f"# {name} - {self._workspace_path_str}/{filename}\n\n{content}")
                        else:
                            parts.append(f"# {name} - {self._workspace_path_str}/{filename}\n\n{self._shift_headings(content, offset=1)}")
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
                parts.append(f"# {name} - {self._workspace_path_str}/{filename}\n\n{content_str}")
            else:
                parts.append(f"# {name} - {self._workspace_path_str}/{filename}\n\n{self._shift_headings(content_str, offset=1)}")

        return "\n\n".join(parts) if parts else ""

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
        session_key: str | None = None,
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

        # Use explicit session_key when available; fall back to channel:chat_id
        # for callers that don't have one (e.g. thread-scoped override).
        sys_session_key = session_key or (f"{channel}:{chat_id}" if channel and chat_id else None)
        sys_static = self.build_system_prompt(
            channel=channel,
            tool_definitions=cs.tool_definitions,
            runtime_context="\n".join(runtime_lines),
            session_key=sys_session_key,
        )
        retained_history = history

        # Dynamic session state — appended to system prompt (changes each turn)
        session_parts: list[str] = []

        _t_log = time.time()
        memory_section = self._build_memory_section()
        _elapsed = (time.time() - _t_log) * 1000
        if _elapsed > 50:
            logger.info("build_messages: _build_memory_section took {:.0f}ms", _elapsed)
        if memory_section:
            session_parts.append(memory_section)

        # tree.json and CURRENT.md are injected via build_instructions_section()
        # (message index 1, close to generation point) as verification standards.

        _t_log = time.time()
        findings_block = self._build_self_findings_section()
        _elapsed = (time.time() - _t_log) * 1000
        if _elapsed > 50:
            logger.info("build_messages: _build_self_findings_section took {:.0f}ms", _elapsed)
        if findings_block:
            session_parts.append(findings_block)

        feedback_block = self._build_user_feedback_section()
        if feedback_block:
            session_parts.append(feedback_block)

        if session_parts:
            sys_static = sys_static + "\n\n" + "\n\n".join(session_parts)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": sys_static},
        ]

        messages.extend(retained_history)

        # Strip image data from history — only the incoming user message keeps
        # its image_url blocks.  Old images and base64-laden tool results are
        # replaced with text placeholders to prevent token bloat across turns.
        _t_log = time.time()
        self._strip_old_images(messages)
        _elapsed = (time.time() - _t_log) * 1000
        if _elapsed > 50:
            logger.info("build_messages: _strip_old_images took {:.0f}ms", _elapsed)

        # Clean user message — no injected metadata
        _t_log = time.time()
        user_content = self._build_user_content(current_message, media)
        if messages[-1].get("role") == current_role:
            # Don't merge into framework-injected assessment/debug_root_cause messages —
            # those are background context injected by _maybe_assess. Merging the real
            # user message into them makes the LLM treat the assessment as user input.
            from nanobot.agent.assess_me import (
                is_assessment_message,
                is_debug_root_cause_message,
            )
            if is_assessment_message(messages[-1]) or is_debug_root_cause_message(messages[-1]):
                messages.append({"role": current_role, "content": user_content})
            else:
                last = dict(messages[-1])
                last["content"] = self._merge_message_content(last.get("content"), user_content)
                messages[-1] = last
        else:
            messages.append({"role": current_role, "content": user_content})

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
    """Return (total_memory_str, available_memory_str), best-effort. Cached after first call."""
    global _memory_info_cache
    if _memory_info_cache is not None:
        return _memory_info_cache
    try:
        import psutil
        mem = psutil.virtual_memory()
        _memory_info_cache = (_fmt_gb(mem.total), _fmt_gb(mem.available))
        return _memory_info_cache
    except ImportError:
        pass
    except Exception:
        logger.warning("Failed to get memory info via psutil", exc_info=True)

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
                    _memory_info_cache = (_fmt_gb(total_kb * 1024), _fmt_gb(free_kb * 1024))
                    return _memory_info_cache
        elif sys.platform == "darwin":
            import os as _os
            total = _os.sysconf('SC_PHYS_PAGES') * _os.sysconf('SC_PAGE_SIZE')
            _memory_info_cache = (_fmt_gb(total), "unknown")
            return _memory_info_cache
        elif sys.platform == "linux":
            with open("/proc/meminfo") as _f:
                total_kb = avail_kb = 0
                for _line in _f:
                    if _line.startswith("MemTotal:"):
                        total_kb = int(_line.split()[1])
                    elif _line.startswith("MemAvailable:"):
                        avail_kb = int(_line.split()[1])
            if total_kb:
                _memory_info_cache = (_fmt_gb(total_kb * 1024), _fmt_gb(avail_kb * 1024))
                return _memory_info_cache
    except Exception:
        logger.warning("Failed to get memory info via platform fallback", exc_info=True)
    _memory_info_cache = ("unknown", "unknown")
    return _memory_info_cache


def _get_gpu_info() -> str | None:
    """Return GPU description string, or None if no GPU detected. Cached after first call."""
    global _gpu_info_cache
    if _gpu_info_cache is not None:
        return _gpu_info_cache if _gpu_info_cache else None
    try:
        import torch
        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            names: list[str] = []
            for i in range(count):
                names.append(torch.cuda.get_device_name(i))
            _gpu_info_cache = ", ".join(names)
            return _gpu_info_cache
    except Exception:
        logger.warning("Failed to detect GPU via torch", exc_info=True)
    import subprocess
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True, timeout=10
        )
        gpus = [line.strip() for line in out.strip().splitlines() if line.strip()]
        if gpus:
            _gpu_info_cache = "; ".join(gpus)
            return _gpu_info_cache
    except Exception:
        logger.warning("Failed to detect GPU via nvidia-smi", exc_info=True)
    _gpu_info_cache = ""
    return None
