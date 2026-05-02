"""Subagent system prompt building."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.agent.context import ContextBuilder


def build_subagent_prompt(
    workspace: Path,
    disabled_skills: set[str],
) -> str:
    """Build a focused system prompt for the subagent."""
    from nanobot.agent.context import ContextBuilder
    from nanobot.agent.skills import SkillsLoader

    time_ctx = ContextBuilder._build_runtime_context(None, None)
    skills_summary = SkillsLoader(
        workspace,
        disabled_skills=disabled_skills,
    ).build_skills_summary()
    return render_template(
        "agent/subagent_system.md",
        time_ctx=time_ctx,
        workspace=str(workspace),
        skills_summary=skills_summary or "",
        context="",
    )