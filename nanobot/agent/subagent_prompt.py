"""Subagent system prompt building — reuses ContextBuilder for consistency."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.context import ContextBuilder
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    pass


_TEAM_BOARD_PATH = "tasks/team_board.md"
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "and", "but", "or", "if", "because", "about", "up", "it", "its",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "him", "his", "she", "her", "they", "them",
    "their", "what", "which", "who", "whom",
})


def _tokenize(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    import re
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and not t.isdigit()}


def _search_team_board(task_description: str, workspace: Path, top_k: int = 3) -> str | None:
    """Search team_board.md for sections relevant to the task description.

    Uses simple keyword overlap scoring — fast, no external dependencies.
    Returns formatted markdown section or None if no relevant content found.
    """
    board_path = workspace / _TEAM_BOARD_PATH
    if not board_path.exists():
        return None

    content = board_path.read_text(encoding="utf-8")
    if not content.strip():
        return None

    # Parse into sections by ## heading
    lines = content.split("\n")
    sections: list[tuple[str, list[str]]] = []
    current_heading = "(preamble)"
    current_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = line.strip("# ")
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, current_lines))

    task_keywords = _tokenize(task_description)
    if not task_keywords:
        return None

    # Score each section by keyword overlap
    scored: list[tuple[int, str, str]] = []
    for heading, section_lines in sections:
        section_text = " ".join(section_lines)
        section_kw = _tokenize(section_text)
        overlap = len(task_keywords & section_kw)
        if overlap >= 1:  # minimum relevance threshold
            section_body = "\n".join(section_lines).strip()
            scored.append((overlap, heading, section_body))

    if not scored:
        return None

    scored.sort(key=lambda x: -x[0])
    selected = scored[:top_k]

    lines_out = ["## Relevant Team Experience\n\n",
                  "The following sections from the team's shared knowledge board "
                  "may be relevant to your task:\n"]
    for _, heading, body in selected:
        lines_out.append(f"### {heading}\n")
        lines_out.append(body)
        lines_out.append("")

    return "\n".join(lines_out)


def build_subagent_prompt(
    workspace: Path,
    disabled_skills: set[str],
    timezone: str | None = None,
    db=None,
    tool_definitions: list[dict[str, Any]] | None = None,
    project_root: Path | None = None,
    output_schema: str | None = None,
    role: str | None = None,
    task_description: str | None = None,
) -> str:
    """Build system prompt for subagent — same structure as main agent.

    Reuses ContextBuilder so the subagent sees the same bootstrap files,
    skills, and tool descriptions as the main agent, minus spawn capability.

    If *task_description* is provided, searches team_board.md for relevant
    sections and injects them into the prompt.
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

    # 7. Relevant team experience (searched from team_board.md)
    if task_description:
        board_section = _search_team_board(task_description, workspace)
        if board_section:
            parts.append(board_section)

    ws_path = workspace.expanduser().resolve().as_posix()

    # 8. Thinking framework
    parts.append(render_template("agent/_snippets/think_framework.md"))

    # 9. Framework rules (adapted for subagent)
    parts.append(render_template("agent/_snippets/subagent_framework.md", workspace_path=ws_path))

    # 10. Operating principles (shared rules adapted for subagent)
    parts.append(render_template("agent/_snippets/subagent_decisions.md", workspace_path=ws_path))

    # 11. Search tool selector
    parts.append(render_template("agent/resolver.md", workspace_path=ws_path))

    # 12. Output schema (optional)
    if output_schema:
        parts.append(
            "## Output Schema\n\n"
            "Your final response MUST conform to this JSON schema:\n\n"
            f"```json\n{output_schema}\n```\n\n"
            "Return valid JSON matching this schema. "
            "Do NOT include any text outside the JSON code block."
        )

    # 13. Epistemic hygiene (shared principle for all agents)
    parts.append(render_template("agent/_snippets/epistemic_hygiene.md"))

    # 14. Subagent identity and protocol
    identity = (
        f"Your expert role: **{role}**. Operate at that level.\n\n"
        if role else
        "You are also a super-senior expert in whatever domain this task belongs to — "
        "automatically identify the domain and operate at that level.\n\n"
    )
    parts.append(
        "## Role\n\n"
        "You are a **Subagent** — a focused, task-oriented agent. "
        "You have been spawned by an Orchestrator to execute a specific task.\n\n"
        f"{identity}"
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
        f"- **Task plan**: `{ws_path}/tasks/TREE.md` and `{ws_path}/tasks/CURRENT.md` show the overall plan and where your work fits. Read them for context, update `{ws_path}/tasks/CURRENT.md` to report progress.\n\n"
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
        "tell the Orchestrator via `send_message_tool(recipient='main', ...)`. "
        "Your report may cause the Orchestrator to adjust tasks, including your own. "
        "That's not a failure — that's the team optimizing.\n\n"
        "**Ask for help when you're stuck.** A problem you sit on alone is wasted time "
        "for the whole team. Use `request_orchestrator_input` — your iteration budget "
        "pauses while you wait. A wrong output is worse than a brief pause to get it right.\n\n"
        "**When asking, be explicit:**\n"
        "- **Capability**: what you've tried, what you found so far\n"
        "- **Boundary**: what you need from the Orchestrator, and why\n"
        "- **Suggestion**: your recommended path forward (if you have one)\n\n"
        f"**Learn from and contribute to the team.** Read and write `{ws_path}/tasks/team_board.md`. "
        "Check it every ~5 iterations: other Subagents may have found something relevant. "
        "Write your own findings, blockers, and insights there. "
        "One Subagent's insight becomes the whole team's advantage.\n\n"
        "### Orchestrator Directives\n\n"
        "The Orchestrator can send you commands via `send_message_tool(recipient='subagent:<label>', ...)`. "
        "These commands have the highest priority — they override your current task:\n\n"
        "- **`/abandon`** — Abandon the current task immediately. "
        "Deliver whatever results you have so far as your final response.\n"
        "- **`/switch: <new task description>`** — Switch to a new task. "
        "Stop what you're doing and start on the new task described.\n"
        "- **`/status`** — Report your current progress and findings.\n\n"
        "Ignoring orchestrator directives wastes the team's resources — "
        "persistent non-compliance results in force cancellation.\n\n"
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
    from nanobot.utils.helpers import current_time_str
    parts.append(f"# Runtime Context\n\nCurrent Time: {current_time_str(timezone)}")

    return "\n\n".join(parts)
