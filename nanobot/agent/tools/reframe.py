"""Reframe tool — distill a problem situation for a focused response.

When the agent is deep in a problem and the accumulated context makes it hard
to think clearly, this tool strips away the noise: it composes a clean problem
statement from the key dimensions (goal, question, attempts, difficulties,
constraints, resources) and sends it to the model for a focused answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.llm_context import chat_stream_with_retry
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        question=p("string", "What's happening? Describe the error, unexpected behavior, or situation you need to figure out."),
        goal=p("string", "What should be true after this is solved — the working state you're aiming for."),
        attempts=p("string", "What has already been tried and what happened."),
        difficulties=p("string", "What went wrong, errors encountered, unexpected behaviour, blockers."),
        constraints=p("string", "Boundaries to respect — time, scope, compatibility, tools, dependencies, conventions."),
        resources=p("string", "Relevant information available — files, data, APIs, docs, references."),
        focus=p("string", "Optional — narrow the response to a specific angle (e.g. 'debugging', 'architecture', 'approach comparison')."),
        required=["question", "goal"],
    )
)
class ReframeTool(Tool):
    """Distill a problem situation for a focused model response."""

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    name = "reframe_tool"
    description = (
        "**Purpose**: Strip away the noise of everything you've tried and get a clean, "
        "focused answer from the model on what to do next. You provide the facts (what "
        "happened, what you tried, what went wrong), the model sees only that — not the "
        "surrounding tool call clutter — and gives you an unbiased direction.\n\n"
        "**When to call — when you need a clean break from the noise**:\n"
        "- Any error or unexpected result — reframe instead of retrying blindly\n"
        "- You tried a few things and the picture is getting messy — reframe to organize "
        "what you know\n"
        "- You have multiple paths forward and need a trade-off analysis\n"
        "- You keep hitting the same wall and need a fresh perspective on the same evidence\n\n"
        "**What to provide**:\n"
        "- `question` and `goal` are required — be precise about what happened and what "
        "you want\n"
        "- Fill `attempts`, `difficulties`, `constraints`, `resources` as needed — more "
        "context = better answer\n\n"
        "**How it differs from `debug_root_cause_tool`**:\n"
        "- `debug_root_cause_tool` recommends an investigation **methodology** "
        "(which RCA approach to use and what to look for)\n"
        "- `reframe_tool` strips tool noise and gets the model to **answer the question "
        "directly** — useful when you already know what the problem is but need a "
        "clear direction\n\n"
        "**Note**: This costs tokens. Be specific for best results."
    )

    read_only = True

    async def execute(
        self,
        question: str,
        goal: str,
        attempts: str = "",
        difficulties: str = "",
        constraints: str = "",
        resources: str = "",
        focus: str = "",
        **kwargs: Any,
    ) -> str:
        lines: list[str] = [
            "You are acting as an independent advisor. The agent is stuck and asking for help.",
            "",
            "## Goal",
            goal,
            "",
            "## Stuck On",
            question,
        ]

        if attempts:
            lines += ["", "## What Has Been Tried", attempts]

        if difficulties:
            lines += ["", "## Difficulties / Blockers", difficulties]

        if constraints:
            lines += ["", "## Constraints", constraints]

        if resources:
            lines += ["", "## Available Resources", resources]

        if focus:
            lines += ["", "## Focus Area", focus]

        # Attach project context if available
        project_root = self._workspace
        if project_root:
            lines += [
                "",
                "## Project Context",
                f"Working directory: {project_root}",
            ]
            project_card = project_root / "project_card.md"
            if project_card.exists():
                try:
                    text = project_card.read_text(encoding="utf-8", errors="replace")
                    lines.append(text[:2000])
                except Exception:
                    logger.exception("Failed to read project_card.md")

        lines += [
            "",
            "## Instructions",
            "- Provide a clear, direct answer — no fluff, no praise",
            "- Be specific: suggest concrete steps, commands, or code changes",
            "- If there are trade-offs, explain them briefly",
            "- If you need more information, say what's missing",
        ]

        prompt = "\n".join(lines)

        try:
            resp = await chat_stream_with_retry([{"role": "user", "content": prompt}])
        except Exception as e:
            return f"Error: LLM call failed — {e}"

        if resp.finish_reason == "error":
            logger.warning("reframe LLM error response: {}", (resp.content or "")[:200])
            return "问题太难，目前没有结论"
        return (resp.content or "").strip() or "问题太难，目前没有结论"
