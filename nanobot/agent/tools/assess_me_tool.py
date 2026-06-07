"""AssessMe tool — read conversation history and validate cognition state."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        focus=p("string", "Optional — narrow assessment: 'direction' (current investigation approach — is it sound?), 'gaps' (what information should I have but don't?), 'assumptions' (unverified beliefs driving my debugging), 'progress' (what's done vs pending). Default = full assessment."),
        verify=p("string", "Optional — specific claims to check against the conversation. Pass each as clear statements. The assessor marks each as ✅ verified, ❌ not verified, or ⚠️ insufficient evidence. E.g.: verify=\"The error is in the SSE parsing, The API key is being passed correctly, I've checked the right file\""),
        required=[],
    )
)
class AssessMeTool(Tool):
    """Audit your own cognition: separate LLM reads the full conversation as a neutral observer and reports what you know, don't know, are assuming, and have left unverified."""

    name = "assess_me_tool"
    description = (
        "**What it does**: A separate LLM reads this entire conversation as a neutral observer "
        "and tells you whether your debugging direction is sound — whether your assumptions "
        "hold up, what you might have missed, and whether you're going in circles. It's an "
        "objective second opinion without leaving the conversation.\n\n"
        "**When to call — when you need a sanity check on your debugging direction**:\n"
        "- A tool returned a confusing result and you're not sure if you misused it or "
        "the problem is elsewhere\n"
        "- You tried a few approaches but keep getting the same or similar errors — "
        "call with `verify=\"my direction is right, the problem is in X\"` to get a pass/fail\n"
        "- You feel like you're going in circles — call with `focus=\"direction\"` to get "
        "an outside view on whether your approach makes sense\n"
        "- You're about to try something expensive (long exec, many greps) and want to "
        "check if your premise is solid first\n"
        "- You have multiple competing hypotheses and want to know which one has the "
        "most evidence behind it\n\n"
        "**Key difference**: This doesn't search code or fetch new information. "
        "It re-reads everything you've already done and tells you if you're on the "
        "right track or wasting time."
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
