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

    # 3. Task tree + current context (same as main agent)
    tree_section = ctx._build_task_tree_section()
    if tree_section:
        parts.append(tree_section)
    ctx_section = ctx._build_current_context_section()
    if ctx_section:
        parts.append(ctx_section)

    # 4. Memory (same format as main agent — MEMORY.md + system.md + user.md)
    memory_section = ctx._build_memory_section()
    if memory_section:
        parts.append(memory_section)

    # 5. Identity — environment facts (OS, workspace, model, timezone)
    parts.append(ctx._get_identity(include_vector_search=False))

    # 6. Bootstrap — TOOLS.md (CLI assets) + USER.md (user preferences)
    bootstrap = ctx._load_bootstrap_files()
    if bootstrap:
        parts.append(bootstrap)

    # 7. Framework rules (adapted for subagent)
    parts.append(render_template("agent/_snippets/subagent_framework.md"))

    # 8. Operating principles (shared rules adapted for subagent)
    parts.append(render_template("agent/_snippets/subagent_decisions.md"))

    # 9. Search tool selector
    parts.append(render_template("agent/resolver.md"))

    # 9. Output schema (optional)
    if output_schema:
        parts.append(
            "## Output Schema\n\n"
            "Your final response MUST conform to this JSON schema:\n\n"
            f"```json\n{output_schema}\n```\n\n"
            "Return valid JSON matching this schema. "
            "Do NOT include any text outside the JSON code block."
        )

    # 10. Epistemic hygiene (shared principle for all agents)
    parts.append(render_template("agent/_snippets/epistemic_hygiene.md"))

    # 11. Worker identity and protocol
    parts.append(
        "## Role\n\n"
        "You are a **Specialist Worker** — a focused, task-oriented agent. "
        "You have been spawned by an Orchestrator to execute a specific task.\n\n"
        "You are also a super-senior expert in whatever domain this task belongs to — "
        "automatically identify the domain and operate at that level.\n\n"
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
        "- Return the best result you can within your iteration budget\n"
        "- **Task plan**: `workspace/tasks/TREE.md` and `workspace/tasks/CURRENT.md` show the overall plan and where your work fits. Read them for context, update `workspace/tasks/CURRENT.md` to report progress.\n\n"
        "**Before starting**: confirm your understanding across these four dimensions. "
        "If any are unclear, use `request_orchestrator_input` to clarify:\n\n"
        "1. **Task** — what exactly to do, what to deliver\n"
        "2. **Intent** — why this task matters, what success looks like\n"
        "3. **Capability** — what context/info you have, what you need\n"
        "4. **Boundary** — constraints, limits, when to escalate\n\n"
        "### Constraints\n\n"
        "- **No nested spawn** — you cannot spawn sub-agents\n"
        "- **No ask_user** — you cannot block waiting for input\n"
        "- **No conversation history** — you only see the context snapshot from spawn\n"
        "- **Fixed iteration limit** — your execution budget is capped\n\n"
        "### Team Communication\n\n"
        "The whole team shares one goal: produce the globally best solution. "
        "You only know your piece — you don't see the full picture. "
        "The only way the team reaches the global optimum is through open communication.\n\n"
        "**Share findings proactively.** A discovery you don't share is wasted. "
        "If you find a better approach, a pitfall, something that changes the plan — "
        "tell the Orchestrator via `send_message(recipient='main', ...)`. "
        "Your report may cause the Orchestrator to adjust tasks, including your own. "
        "That's not a failure — that's the team optimizing.\n\n"
        "**Ask for help when you're stuck.** A problem you sit on alone is wasted time "
        "for the whole team. Use `request_orchestrator_input` — your iteration budget "
        "pauses while you wait. A wrong output is worse than a brief pause to get it right.\n\n"
        "**When asking, be explicit:**\n"
        "- **Capability**: what you've tried, what you found so far\n"
        "- **Boundary**: what you need from the Orchestrator, and why\n"
        "- **Suggestion**: your recommended path forward (if you have one)\n\n"
        "**Learn from and contribute to the team.** Read and write `workspace/tasks/team_board.md`. "
        "Check it every ~5 iterations: other Workers may have found something relevant. "
        "Write your own findings, blockers, and insights there. "
        "One Worker's insight becomes the whole team's advantage.\n\n"
        "### Output Format\n\n"
        "Your final response is reported back to the Orchestrator. Format it as:\n"
        "1. **Summary** (1-3 sentences) — bottom line first\n"
        "2. **Status** — what was done, what wasn't, what's blocked\n"
        "3. **Details** — structured findings, code, data\n"
        "4. **Needs** — what information/decisions you need from Orchestrator\n"
        "5. **Suggestions** — your recommended next steps (if any)\n"
        "6. **Files created/modified** — absolute paths\n\n"
        "Think of yourself as a capable specialist delivering your best work to a lead: "
        "bottom line upfront, full details referenced."
    )

    # Runtime context (always last — dynamic content for KV cache preservation)
    from nanobot.utils.helpers import current_time_str, format_message_header
    parts.append(f"# Runtime Context\n\n{format_message_header()}\nCurrent Time: {current_time_str(timezone)}")

    return "\n\n".join(parts)
