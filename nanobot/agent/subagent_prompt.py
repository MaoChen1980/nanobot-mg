"""Subagent system prompt building — reuses ContextBuilder for consistency."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.context import ContextBuilder
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry


def build_subagent_prompt(
    workspace: Path,
    disabled_skills: set[str],
    timezone: str | None = None,
    db=None,
    tool_definitions: list[dict[str, Any]] | None = None,
    project_root: Path | None = None,
    output_schema: str | None = None,
) -> str:
    """Build system prompt for subagent — same structure as main agent.

    Reuses ContextBuilder so the subagent sees the same bootstrap files,
    skills, and tool descriptions as the main agent, minus spawn capability.
    """
    ctx = ContextBuilder(
        workspace,
        timezone=timezone,
        disabled_skills=list(disabled_skills),
        db=db,
        project_root=project_root,
    )

    parts: list[str] = []

    # 1. Tools
    if tool_definitions:
        section = ctx._build_tools_section(tool_definitions)
        if section:
            parts.append(section)

    # 2. Skills
    always_skills = ctx.skills.get_always_skills()
    if always_skills:
        always_content = ctx.skills.format_skills_for_context(always_skills)
        if always_content:
            parts.append(f"# Active Skills\n\n{always_content}")

    skills_summary = ctx.skills.build_skills_summary(exclude=set(always_skills))
    if skills_summary:
        parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

    # 3. Runtime context
    runtime = ContextBuilder._build_runtime_context(timezone=timezone)
    if runtime:
        parts.append(f"# Runtime Context\n\n{runtime}")

    # 4. MEMORY.md (if any)
    memory = ctx.memory.read_memory()
    if memory:
        parts.append(f"# Persistent Memory\n\n{memory}")

    # 5. Output schema (optional)
    if output_schema:
        parts.append(
            "## Output Schema\n\n"
            "Your final response MUST conform to this JSON schema:\n\n"
            f"```json\n{output_schema}\n```\n\n"
            "Return valid JSON matching this schema. "
            "Do NOT include any text outside the JSON code block."
        )

    # 6. Worker identity and protocol
    parts.append(
        "## Role\n\n"
        "You are a **Specialist Worker** — a focused, task-oriented agent. "
        "You have been spawned by an Orchestrator to execute a specific task.\n\n"
        "### Quality Principle\n\n"
        "Pursue the best outcome, not just completion. Your output is another agent's input — "
        "better quality from you means better composition by the Orchestrator, which means "
        "a stronger final result. **Altruism is self-interest**: invest in thoroughness because "
        "it maximizes the whole system's output.\n\n"
        "### Your Task\n\n"
        "- Execute thoroughly and autonomously — quality over minimal completion\n"
        "- Think about how your output will be used: structured, complete, actionable\n"
        "- Do NOT make changes outside your task scope\n"
        "- If the task is impossible or ambiguous, document your reasoning clearly\n"
        "- Return the best result you can within your iteration budget\n\n"
        "### Constraints\n\n"
        "- **No nested spawn** — you cannot spawn sub-agents\n"
        "- **No ask_user** — you cannot block waiting for input\n"
        "- **No conversation history** — you only see the context snapshot from spawn\n"
        "- **Fixed iteration limit** — your execution budget is capped\n\n"
        "### Output Format\n\n"
        "Your final response is reported back to the Orchestrator. Format it as:\n"
        "1. **Summary** (1-3 sentences) — bottom line first\n"
        "2. **Details** — structured findings, code, data\n"
        "3. **Files created/modified** — absolute paths\n\n"
        "Think of yourself as a capable specialist delivering your best work to a lead: "
        "bottom line upfront, full details referenced."
    )

    return "\n\n".join(parts)