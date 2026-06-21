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


def _search_team_board(task_description: str, workspace: Path, top_k: int = 3, board_rel: str | None = None) -> str | None:
    """Search team_board.md for sections relevant to the task description.

    Uses simple keyword overlap scoring — fast, no external dependencies.
    Returns formatted markdown section or None if no relevant content found.
    *board_rel*: session-scoped relative path (e.g. ``tasks/team_board_cli_direct.md``).
    """
    board_path = workspace / (board_rel or _TEAM_BOARD_PATH)
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

    lines_out = ["## Relevant Project Facts\n\n",
                  "The following sections from the project's fact board (team_board.md) "
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
    session_key: str | None = None,
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

    # Session-scoped file paths
    from nanobot.agent.context import _sanitize_session_key
    suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
    tree_rel = f"tasks/tree{suffix}.json"
    current_rel = f"tasks/CURRENT{suffix}.md"
    team_board_rel = f"tasks/team_board{suffix}.md"

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

    # 3. Task tree + current context (same as main agent, session-scoped)
    tree_section = ctx._build_task_tree_section(session_key=session_key)
    if tree_section:
        parts.append(tree_section)
    ctx_section = ctx._build_current_context_section(session_key=session_key)
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

    # 7. Relevant team experience (searched from session-scoped team_board.md)
    if task_description:
        board_rel = team_board_rel  # use session-scoped path
        board_section = _search_team_board(task_description, workspace, board_rel=board_rel)
        if board_section:
            parts.append(board_section)

    ws_path = workspace.expanduser().resolve().as_posix()

    # 8. Framework rules (adapted for subagent — how the system works)
    fw_content = render_template("agent/_snippets/subagent_framework.md",
        workspace_path=ws_path,
        tree_path=f"{ws_path}/{tree_rel}",
        current_path=f"{ws_path}/{current_rel}",
        team_board_path=f"{ws_path}/{team_board_rel}",
        tree_rel=tree_rel,
        current_rel=current_rel,
        team_board_rel=team_board_rel,
        max_iterations=ctx._framework_config.get("max_iterations", 200),
        context_window_tokens=ctx._framework_config.get("context_window_tokens", 200_000),
        max_tool_result_chars=ctx._framework_config.get("max_tool_result_chars", 32_000),
        exec_timeout=ctx._framework_config.get("exec_timeout", 60),
    )
    # Post-process old path references in the template
    if suffix:
        old_tree = f"{ws_path}/tasks/tree.json"
        old_current = f"{ws_path}/tasks/CURRENT.md"
        old_board = f"{ws_path}/tasks/team_board.md"
        fw_content = fw_content.replace(old_tree, f"{ws_path}/{tree_rel}")
        fw_content = fw_content.replace(old_current, f"{ws_path}/{current_rel}")
        fw_content = fw_content.replace(old_board, f"{ws_path}/{team_board_rel}")
    parts.append(fw_content)

    # 9. Search tool selector
    parts.append(render_template("agent/resolver.md", workspace_path=ws_path))

    # 10. Output schema (optional)
    if output_schema:
        parts.append(
            "## Output Schema\n\n"
            "Your final response MUST conform to this JSON schema:\n\n"
            f"```json\n{output_schema}\n```\n\n"
            "Return valid JSON matching this schema. "
            "Do NOT include any text outside the JSON code block."
        )

    # 11. Role identity and constraints (reference — who the subagent is)
    role_line = (
        f"Your expert role: **{role}**. Operate at that level."
        if role else
        "You are also a super-senior expert in whatever domain this task belongs to — "
        "automatically identify the domain and operate at that level."
    )
    parts.append(
        "## Role\n\n"
        "You are a **Subagent** — a focused, task-oriented agent. "
        "You have been spawned by an Orchestrator to execute a specific task.\n\n"
        f"{role_line}\n\n"
        "### Constraints\n\n"
        "- **No nested spawn** — you cannot spawn sub-agents\n"
        "- **No ask_user** — you cannot block waiting for input\n"
        "- **No request_orchestrator_input_tool** — you cannot block waiting for the Orchestrator\n"
        "- **No conversation history** — you only see the context snapshot from spawn\n"
        "- **Fixed iteration limit** — your execution budget is capped\n"
        "- **Blocked? Fail directly** — if you cannot complete the task, report the blocker via "
        "`send_message_tool(recipient='main', ...)` with what you tried and what's missing, "
        "then stop. The Orchestrator will re-spawn with better instructions."
    )

    # Runtime context (always last — dynamic content for KV cache preservation)
    from nanobot.utils.helpers import current_time_str
    parts.append(f"# Runtime Context\n\nCurrent Time: {current_time_str(timezone)}")

    return "\n\n".join(parts)
