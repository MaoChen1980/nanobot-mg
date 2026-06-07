"""Reframe tool — distill a problem situation for a focused response.

When the agent is deep in a problem and the accumulated context makes it hard
to think clearly, this tool strips away the noise: it composes a clean problem
statement from the key dimensions (goal, question, attempts, difficulties,
constraints, resources) and sends it to the model for a focused answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@tool_parameters(
    build_parameters_schema(
        question=p("string", "The specific problem or question to solve — describe it clearly and precisely."),
        goal=p("string", "The desired outcome. What should be true after this is solved."),
        attempts=p("string", "What has already been tried and what happened."),
        difficulties=p("string", "What went wrong, errors encountered, unexpected behaviour, blockers."),
        constraints=p("string", "Boundaries to respect — time, scope, compatibility, tools, dependencies, conventions."),
        resources=p("string", "Relevant information available — files, data, APIs, docs, references."),
        focus=p("string", "Optional — narrow the response to a specific angle (e.g. 'architecture', 'debugging', 'approach comparison')."),
        required=["question", "goal"],
    )
)
class ReframeTool(Tool):
    """Distill a problem situation for a focused model response."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    name = "reframe_tool"
    description = (
        "**Purpose**: Collect everything known about a problem (goal, what was tried, "
        "what went wrong, constraints, available resources) into a clean problem statement "
        "and send it to the model for a focused, unbiased answer.\n\n"
        "**When to use — you are in one of these concrete situations**:\n"
        "- A tool just returned an error or unexpected result, and retrying the same thing "
        "probably won't help — reframe with the error details before attempting a different fix\n"
        "- Several tools were called in a row but you are still not closer to the goal — "
        "reframe to identify what is missing or misdirected\n"
        "- You are about to choose between multiple approaches and want a trade-off analysis "
        "without the noise of previous tool calls\n"
        "- Tool results contradicted your assumptions — reframe to incorporate the new information\n"
        "- The problem is complex with many interdependent parts — reframe to organize before "
        "diving into implementation\n\n"
        "**What to provide**:\n"
        "- `question` and `goal` are required — be precise\n"
        "- Fill `attempts`, `difficulties`, `constraints`, `resources` as needed\n\n"
        "**How it works**: All inputs are composed into a clean standalone prompt. "
        "The model sees only this distilled summary — not the surrounding tool noise — "
        "giving you a clear answer.\n\n"
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
        loop = self._loop

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
        project_root = getattr(loop, "workspace", None)
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
                    pass

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
            resp = await loop.provider.chat_stream(
                [{"role": "user", "content": prompt}],
                model=loop.model,
            )
        except Exception as e:
            return f"Error: LLM call failed — {e}"

        return (resp.content or "").strip() or "(empty response)"
