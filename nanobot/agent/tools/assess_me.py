"""AssessMe tool — read conversation history and validate cognition state."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        focus=p("string", "Optional — narrow assessment: 'direction' (current investigation approach — is it sound?), 'gaps' (what information should I have but don't?), 'assumptions' (unverified beliefs driving my debugging), 'progress' (what's done vs pending). Default = full assessment.",
            enum=["direction", "gaps", "assumptions", "progress"]),
        verify=p("string", "Optional — specific claims to check against the conversation. Pass each as clear statements. The assessor marks each as ✅ verified, ❌ not verified, or ⚠️ insufficient evidence. E.g.: verify=\"The error is in the SSE parsing, The API key is being passed correctly, I've checked the right file\""),
        required=[],
    )
)
class AssessMeTool(Tool):
    """Audit your own cognition: separate LLM reads the full conversation as a neutral observer and reports what you know, don't know, are assuming, and have left unverified."""
    instruction = (
        "Get a second LLM evaluation when unsure about direction or correctness. "
        "Call when: a tool returned confusing results, you're going in circles, "
        "you have multiple competing hypotheses, or before doing something expensive "
        "to check if your premise is solid."
    )

    name = "assess_me"
    description = (
        "A separate LLM reads this entire conversation as a neutral observer and reports "
        "whether your debugging direction is sound, what assumptions need verification, "
        "and what you might have missed. Focus options: direction, gaps, assumptions, "
        "progress. Also supports verify= for pass/fail on specific claims."
        "\n\nOutput example:\n"
        "  Assessment: direction is sound.\n"
        "  Verify assumption: DB is the bottleneck."
    )
    read_only = True

    def __init__(self) -> None:
        self._messages: ContextVar[list[dict[str, Any]]] = ContextVar(
            "assess_me_messages", default=[]
        )

    def set_context(self, messages: list[dict[str, Any]]) -> None:
        """Set the conversation messages for assessment."""
        self._messages.set(messages)

    async def execute(
        self,
        focus: str = "",
        verify: str = "",
        **kwargs: Any,
    ) -> str:
        messages = self._messages.get()
        if not messages:
            return "Error: no active session — cannot read conversation history."

        from nanobot.agent.assess_me import assess_me

        try:
            result = await assess_me(messages, verify=verify)
        except Exception as e:
            return f"Error: assessment LLM call failed — {e}"

        if not result:
            return "Error: assessment LLM returned empty response."

        if focus:
            result = f"Focus: {focus}\n\n{result}"

        return result