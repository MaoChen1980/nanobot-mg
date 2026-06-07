"""AssessMe tool — read conversation history and validate cognition state."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@tool_parameters(
    build_parameters_schema(
        focus=p("string", "Optional — narrow assessment to one area: 'progress' (what's done vs pending and what's blocking), 'gaps' (information I should have but haven't collected), 'assumptions' (unverified beliefs driving my reasoning), 'files' (what I've read/modified vs what I haven't touched yet). Default (empty) = full 7-question assessment."),
        required=[],
    )
)
class AssessMeTool(Tool):
    """Audit your own cognition: separate LLM reads the full conversation as a neutral observer and reports what you know, don't know, are assuming, and have left unverified."""

    name = "assess_me_tool"
    description = (
        "**What it does**: A separate LLM reads this entire conversation as a neutral observer "
        "and answers 7 questions: what you've done, what you know, what you don't know, "
        "what you're doing now, what you need but haven't got, what you're assuming without "
        "verifying, and what your current goal and priority are.\n\n"
        "**When to call — you are in one of these situations**:\n"
        "1. You just read several files and are about to summarize or act — "
        "call this to check whether you actually confirmed the structure or filled in gaps with assumptions\n"
        "2. You got an error or unexpected tool result — "
        "call this to separate what you know from what you guessed\n"
        "3. You're planning next steps and multiple paths exist — "
        "call this to identify what you're assuming and haven't verified yet\n"
        "4. You notice yourself repeating the same pattern — "
        "call this to find what you overlooked\n"
        "5. The conversation is long and you lost track of what's been done — "
        "call this to get a progress summary\n\n"
        "**Key difference from other tools**: This doesn't fetch external information. "
        "It re-reads everything you already have and tells you what you actually know "
        "vs what you think you know. Call it when you need to step back and see the whole picture."
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
        **kwargs: Any,
    ) -> str:
        loop = self._loop
        session_key = self._session_key.get()
        if not session_key:
            return "Error: no active session — cannot read conversation history."

        session = loop.sessions.get_or_create(session_key)
        history = session.format_history(
            include_timestamps=True, timezone=loop.context.timezone
        )
        if not history:
            return "Error: conversation history is empty."

        from nanobot.agent.assess_me import assess_me

        result = await assess_me(history, loop.provider, loop.model)
        if result is None:
            return "Error: assessment LLM call failed."

        if focus:
            result = f"Focus: {focus}\n\n{result}"

        return f"No response needed, but a reminder:\n\n{result}"
