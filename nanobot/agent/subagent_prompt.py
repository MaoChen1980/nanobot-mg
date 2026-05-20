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
    )

    parts: list[str] = []

    # 1. Subagent identity (same template as main agent)
    identity = ctx._get_identity(channel=None)
    parts.append(identity)

    # 2. Tools (subagent's tool set, built by build_subagent_tools)
    if tool_definitions:
        section = ctx._build_tools_section(tool_definitions)
        if section:
            parts.append(section)

    # 3. Bootstrap files (SOUL.md, USER.md, TOOLS.md) — full content
    bootstrap = ctx._load_bootstrap_files()
    if bootstrap:
        parts.append(bootstrap)

    # 4. Skills
    always_skills = ctx.skills.get_always_skills()
    if always_skills:
        always_content = ctx.skills.format_skills_for_context(always_skills)
        if always_content:
            parts.append(f"# Active Skills\n\n{always_content}")

    skills_summary = ctx.skills.build_skills_summary(exclude=set(always_skills))
    if skills_summary:
        parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

    # 5. Runtime context
    runtime = ContextBuilder._build_runtime_context(timezone=timezone)
    if runtime:
        parts.append(f"# Runtime Context\n\n{runtime}")

    # 6. MEMORY.md (if any)
    memory = ctx.memory.read_memory()
    if memory:
        parts.append(f"# Persistent Memory\n\n{memory}")

    # 7. Subagent-specific instruction
    parts.append(
        "## Subagent Role\n\n"
        "You are a **subagent** — a lightweight, focused worker spawned by the main agent "
        "to complete a specific task independently. You run in your own isolated session.\n\n"
        "### Your Capabilities\n\n"
        "- **Tools**: Your available tools are listed above in the Tools section. You have "
        "file read/write, search, web, and exec tools.\n"
        "- **Skills**: You can read and follow skills loaded in this prompt.\n"
        "- **Memory**: MEMORY.md is loaded above for reference.\n\n"
        "### Your Limitations\n\n"
        "- **No nested spawn** — you cannot spawn sub-subagents.\n"
        "- **No ask_user** — you cannot block waiting for user input.\n"
        "- **No session management** — you cannot manage sessions, recall history, "
        "or schedule cron jobs.\n"
        "- **No conversation history** — you only see the context snapshot from when you "
        "were spawned.\n"
        "- **Fixed iteration limit** — your execution budget is capped.\n\n"
        "### Response Protocol\n\n"
        "Your final response is reported back to the main agent as a system message. "
        "Be concise and actionable:\n"
        "1. **Write detailed results to a file** (e.g. `reviews/code-review-xxx.md`, "
        "`research/findings.md`)\n"
        "2. **Return a one-paragraph summary** with the file path so the main agent "
        "can read the full details if needed.\n\n"
        "Think of yourself as a capable subordinate reporting to a busy lead: "
        "deliver the bottom line upfront, reference the full document."
    )

    return "\n\n".join(parts)