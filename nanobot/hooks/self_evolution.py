"""SelfEvolutionEngine — Read .pt + codebase, spawn subagent to analyze and fix defects.

Triggered by a system_event cron job. The engine reads recent .pt files and
relevant codebase files, builds a comprehensive context, then spawns a
subagent (with its own AgentLoop) to analyze and fix the defects.

The subagent sees everything in its initial messages — no path discovery needed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.agent.memory_store import MemoryStore

# Max chars of codebase content sent to the subagent
_CODEBASE_MAX_CHARS = 80_000

# Which template files to include in the analysis
_TEMPLATE_FILES = [
    "identity.md",
    "system_prompt.md",
    "_snippets/framework_core.md",
    "_instructions/operating_principles.md",
    "_instructions/think_triggers.md",
    "_instructions/task_tree.md",
    "_instructions/output_rules.md",
    "_instructions/skill_refinement.md",
    "_instructions/meta_learning.md",
]

# Key hook files to include
_HOOK_FILES = [
    "hooks/self_fix_hook.py",
    "hooks/self_detect_hook.py",
]


class SelfEvolutionEngine:
    """Read .pt + codebase, spawn subagent to find and fix system defects."""

    def __init__(self, store: MemoryStore, project_root: str | Path | None = None):
        self.store = store
        self.prompts_dir = ensure_dir(store.workspace / "prompts")
        self.project_root = (
            Path(project_root).resolve() if project_root
            else Path(__file__).resolve().parent.parent.parent  # nanobot/ -> project root
        )
        self.templates_dir = self.project_root / "nanobot" / "templates" / "agent"
        self.hooks_dir = self.project_root / "nanobot"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, max_pt_files: int = 3) -> bool:
        """Run self-evolution analysis.

        Returns True if a subagent was spawned (regardless of outcome).
        """
        # 1. Find .pt files from the past 24h
        pt_files = self._get_recent_pt_files(hours=24)
        if not pt_files:
            logger.info("SelfEvolution: no .pt files from past 24h")
            return False

        logger.info("SelfEvolution: {} .pt files in window", len(pt_files))

        # 2. Read codebase files
        codebase_sections = self._read_codebase()
        if not codebase_sections:
            logger.info("SelfEvolution: no codebase files found")
            return False

        # 3. Build analysis context
        user_parts: list[str] = []
        for pt_path in pt_files[:max_pt_files]:
            pt_text = self._read_pt_for_analysis(pt_path)
            if pt_text:
                user_parts.append(f"## .pt: {pt_path.name}\n\n{pt_text}")

        codebase_text = "\n\n---\n\n".join(codebase_sections)
        user_parts.append(f"## Current Codebase\n\n{codebase_text}")

        user_content = (
            "以下是过去 24h 的 .pt 对话快照和当前 codebase。"
            "请按 system prompt 中的步骤分析并修复缺陷。\n\n"
            + "\n\n---\n\n".join(user_parts)
        )

        if len(user_content) > _CODEBASE_MAX_CHARS * 2:
            user_content = user_content[:_CODEBASE_MAX_CHARS * 2] + "\n\n... (truncated)"

        # 4. Spawn subagent
        await self._spawn_subagent(user_content)
        return True

    # ------------------------------------------------------------------
    # .pt file discovery
    # ------------------------------------------------------------------

    def _get_recent_pt_files(self, hours: int = 24) -> list[Path]:
        """Return .pt files created within the last *hours*, sorted newest first."""
        if not self.prompts_dir.is_dir():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        # Filename format: {session_key}-YYYY-MM-DDTHH-MM-SS.pt
        pattern = re.compile(r".*-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})\.pt$")

        candidates: list[tuple[Path, datetime]] = []
        for f in sorted(self.prompts_dir.iterdir(), reverse=True):
            if f.suffix != ".pt":
                continue
            m = pattern.search(f.name)
            if m:
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S")
                    ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        candidates.append((f, ts))
                except ValueError:
                    continue

        # Sort newest first
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in candidates]

    @staticmethod
    def _read_pt_for_analysis(pt_path: Path) -> str | None:
        """Read .pt file, extract key parts for analysis."""
        try:
            data = json.loads(pt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("SelfEvolution: failed to read {}", pt_path)
            return None

        saved_at = data.get("saved_at", "")
        session_key = data.get("session_key", "")
        messages = data.get("messages", [])

        # Extract system prompt (usually messages[0])
        system_prompt = ""
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                system_prompt = content[:5000]  # first 5K chars
                break

        # Build conversation summary: keep user corrections + tool calls
        conv_lines = [f"Session: {session_key}", f"Saved at: {saved_at}"]
        if system_prompt:
            conv_lines.append(f"\n### System Prompt (first 5K chars)\n{system_prompt}")

        conv_lines.append("\n### Conversation Highlights")
        for m in messages[-20:]:  # keep last 20 messages
            role = m.get("role", "?")
            content = m.get("content", "")
            tc = m.get("tool_calls")

            if role == "user" and isinstance(content, str):
                # Keep user messages
                truncated = content[:500]
                conv_lines.append(f"\n[user] {truncated}")
            elif role == "assistant" and isinstance(content, str):
                # Only keep assistant messages that are short or have tool calls
                if tc:
                    tools_str = ", ".join(
                        t.get("function", {}).get("name", "?") for t in tc
                    )
                    conv_lines.append(f"\n[assistant (tools: {tools_str})] {content[:300]}")
                else:
                    conv_lines.append(f"\n[assistant] {content[:200]}")
            elif role == "tool":
                # Skip tool results in detail, just note them
                pass

        result = "\n".join(conv_lines)
        if len(result) > _CODEBASE_MAX_CHARS:
            result = result[:_CODEBASE_MAX_CHARS] + "\n\n... (truncated)"
        return result

    # ------------------------------------------------------------------
    # Codebase reading
    # ------------------------------------------------------------------

    def _read_codebase(self) -> list[str]:
        """Read relevant codebase files and return as list of text sections."""
        sections: list[str] = []

        # Read prompt templates
        for rel_path in _TEMPLATE_FILES:
            full_path = self.templates_dir / rel_path
            if not full_path.exists():
                continue
            try:
                text = full_path.read_text(encoding="utf-8")
                sections.append(f"### templates/agent/{rel_path}\n\n{text}")
            except OSError:
                continue

        # Read hook files
        for rel_path in _HOOK_FILES:
            full_path = self.hooks_dir / rel_path
            if not full_path.exists():
                continue
            try:
                text = full_path.read_text(encoding="utf-8")
                if len(text) > 3000:
                    text = text[:3000] + "\n\n... (truncated)"
                sections.append(f"### nanobot/{rel_path}\n\n{text}")
            except OSError:
                continue

        return sections

    # ------------------------------------------------------------------
    # Subagent spawning
    # ------------------------------------------------------------------

    async def _spawn_subagent(self, user_content: str) -> None:
        """Spawn a subagent to analyze and fix defects."""
        from nanobot.agent.llm_context import _llm_model, _llm_provider
        from nanobot.agent.runner import AgentRunner, AgentRunSpec
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.search import GlobTool, GrepTool
        from nanobot.agent.tools.shell.shell import ExecTool

        try:
            provider = _llm_provider.get()
            model = _llm_model.get()
        except LookupError:
            logger.warning("SelfEvolution: LLM provider not available")
            return

        ws_path = self.store.workspace.expanduser().resolve().as_posix()
        project_root_str = self.project_root.as_posix()

        tools = ToolRegistry()
        # File tools scoped to project root (for modifying codebase files)
        tools.register(ReadFileTool(workspace=self.project_root))
        tools.register(WriteFileTool(workspace=self.project_root))
        tools.register(EditFileTool(workspace=self.project_root))
        tools.register(GlobTool(workspace=self.project_root))
        tools.register(GrepTool(workspace=self.project_root))
        tools.register(ExecTool(
            working_dir=project_root_str,
            timeout=120,
        ))

        system_prompt = render_template(
            "agent/evolution_agent.md",
            workspace_path=ws_path,
            project_root=project_root_str,
        )

        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            tools=tools,
            model=model,
            max_iterations=40,
            max_tool_result_chars=10000,
        )

        runner = AgentRunner(provider)
        try:
            await runner.run(spec)
            logger.info("SelfEvolution: subagent completed")
        except Exception:
            logger.exception("SelfEvolution: subagent failed")
