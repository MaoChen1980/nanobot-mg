"""AssessMe tool — read conversation history and validate cognition state."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@tool_parameters(
    build_parameters_schema(
        focus=p("string", "Optional — narrow assessment to one area: 'progress' (what's done vs pending and what's blocking), 'gaps' (information I should have but haven't collected), 'assumptions' (unverified beliefs driving my reasoning), 'files' (what I've read/modified vs what I haven't touched yet). Default (empty) = full 7-question assessment."),
        verify=p("string", "Optional — specific items for the assessor to check against conversation history. Pass each item as a clear claim or statement. The assessor will mark each as ✅ verified, ❌ not verified (contradicted), or ⚠️ insufficient evidence. E.g.: 'verify=\"The config file is at /etc/app/config.yml, The API returns JSON, Port 8080 is open\"'"),
        required=[],
    )
)
class AssessMeTool(Tool):
    """Audit your own cognition: separate LLM reads the full conversation as a neutral observer and reports what you know, don't know, are assuming, and have left unverified."""

    name = "assess_me_tool"
    description = (
        "**What it does**: A separate LLM reads this entire conversation as a neutral observer "
        "and either (a) answers 7 questions about what you know/don't know/are assuming, or "
        "(b) verifies specific items you pass via the `verify` parameter.\n\n"
        "**When to call — you are in one of these situations**:\n"
        "1. You just listed premises and want them verified — "
        "call with `verify=\"premise 1, premise 2, ...\"` to get pass/fail per item\n"
        "2. You need to check if a claim is supported by what you've done — "
        "call with `verify=\"claim\"` to confirm or refute\n"
        "3. You're planning next steps and multiple paths exist — "
        "call with `focus=\"assumptions\"` to identify unverified beliefs\n"
        "4. The conversation is long and you lost track — "
        "call with `focus=\"progress\"` to get a summary\n\n"
        "**Key difference from other tools**: This doesn't fetch external information. "
        "It re-reads everything you already have and tells you what you actually know "
        "vs what you think you know."
    )
    read_only = True

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop
        self._session_key: ContextVar[str] = ContextVar(
            "assess_me_session_key", default=""
        )

    def set_context(self, session_key: str) -> None:
        """Set the session key for reading conversation history."""
        self._session_key.set(session_key)

    async def execute(
        self,
        focus: str = "",
        verify: str = "",
        **kwargs: Any,
    ) -> str:
        session_key = self._session_key.get()
        if not session_key:
            return "Error: no active session — cannot read conversation history."

        session = self._loop.sessions.get_or_create(session_key)
        history = session.format_history(
            include_timestamps=True, timezone=self._loop.context.timezone
        )
        if not history:
            return "Error: conversation history is empty."

        from nanobot.agent.assess_me import assess_me

        result = await assess_me(history, verify=verify)
        if result is None:
            return "Error: assessment LLM call failed."

        if focus:
            result = f"Focus: {focus}\n\n{result}"

        return f"No response needed, but a reminder:\n\n{result}"
