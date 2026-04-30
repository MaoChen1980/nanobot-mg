"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, current_time_str, detect_image_mime, truncate_text
from nanobot.utils.prompt_templates import render_template


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context]"
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_CHARS = 32_000  # hard cap on recent history section size
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity(channel=channel)]

        # Current State — merged block: Goals + HEARTBEAT tasks + SESSION.md
        state_block = self._build_state_section()
        if state_block:
            parts.append(f"# Current State\n\n{state_block}")

        # Memory before rules — established facts should precede constraints
        memory = self.memory.get_memory_context()
        if memory and not self._is_template_content(self.memory.read_memory(), "memory/MEMORY.md"):
            parts.append(f"# Memory\n\n{memory}")

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

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            history_text = "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            )
            history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
            parts.append("# Recent History\n\n" + history_text)

        # Tools at end — reference section, not reasoning section
        if tool_definitions:
            section = self._build_tools_section(tool_definitions)
            if section:
                parts.append(section)

        return "\n\n---\n\n".join(parts)

    def _build_tools_section(self, tool_definitions: list[dict[str, Any]]) -> str:
        """Build the available tools section for the system prompt."""
        if not tool_definitions:
            return ""
        lines = ["# Available Tools\n"]
        for schema in tool_definitions:
            fn = schema.get("function", {})
            name = fn.get("name", "unknown")
            desc = fn.get("description", "")
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    def _build_state_section(self) -> str:
        """Build a merged Current State block from Goals + SESSION.md + process-log.

        Note: HEARTBEAT active tasks are NOT injected here — they are embedded
        directly in heartbeat messages by the heartbeat service (service.py).
        """
        blocks = []

        goals = self.memory.read_file(self.workspace / "memory" / "goals.md")
        if goals and not self._is_template_content(goals, "memory/goals.md"):
            goals = goals.removeprefix("# Goals\n").removeprefix("# Goals\r\n")
            lines = goals.split("\n")
            goal_lines = [
                l for l in lines
                if not (l.strip().startswith(">") and ("\u6700\u540e\u66f4\u65b0" in l or "Last updated" in l))
            ]
            blocks.append("## Goals\n\n" + "\n".join(goal_lines).strip())

        session_file = self.workspace / "SESSION.md"
        if session_file.exists():
            lines = session_file.read_text(encoding="utf-8").strip().split("\n")
            summary = "\n".join(line for line in lines[:3] if line.strip())
            if summary:
                blocks.append("## Session\n\n" + summary)

        # Process log — last 5 substantive entries for cross-turn continuity
        plog_file = self.workspace / "memory" / "process-log.md"
        if plog_file.exists():
            plog_content = plog_file.read_text(encoding="utf-8")
            entries = []
            for line in plog_content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("### [") and "跳过" not in stripped and "干净" not in stripped:
                    entries.append(stripped)
            recent = entries[-5:]
            if recent:
                blocks.append("## Recent Progress\n\n" + "\n".join(recent))

        return "\n\n".join(blocks) if blocks else ""

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None,
        model: str | None = None,
        context_window_tokens: int | None = None,
        context_used_tokens: int | None = None,
        cached_tokens: int | None = None,
        current_iteration: int | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if model:
            lines.append(f"Model: {model}")
        if context_window_tokens is not None and context_used_tokens is not None:
            pct = int(100 * context_used_tokens / context_window_tokens) if context_window_tokens else 0
            lines.append(f"Context: {pct}% ({context_used_tokens:,}/{context_window_tokens:,} tokens)")
            if pct >= 70:
                lines.append(f"⚠ Context usage high ({pct}%) — consider summarization")
        if cached_tokens is not None and cached_tokens > 0:
            lines.append(f"Cache: {cached_tokens:,} tokens reused")
        if current_iteration is not None and max_iterations is not None:
            lines.append(f"Iteration: {current_iteration}/{max_iterations}")
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        return (
            ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" +
            "\n".join(lines) + "\n" +
            ContextBuilder._RUNTIME_CONTEXT_END +
            "\n\n══════ Current Turn ══════"
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
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if tpl.is_file():
                return content.strip() == tpl.read_text(encoding="utf-8").strip()
        except Exception:
            pass
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
        session_summary: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        model: str | None = None,
        context_window_tokens: int | None = None,
        context_used_tokens: int | None = None,
        cached_tokens: int | None = None,
        current_iteration: int | None = None,
        max_iterations: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone, session_summary=session_summary,
            model=model,
            context_window_tokens=context_window_tokens,
            context_used_tokens=context_used_tokens,
            cached_tokens=cached_tokens,
            current_iteration=current_iteration,
            max_iterations=max_iterations,
        )
        user_content = self._build_user_content(current_message, media)
        merged = self._merge_message_content(runtime_ctx, user_content)
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel, tool_definitions=tool_definitions)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
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
