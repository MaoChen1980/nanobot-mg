"""Debug root-cause tool — read session history and suggest investigation direction.

When tools fail repeatedly, this tool reads the full conversation and applies
8 root-cause-analysis methods (divide & conquer, comparison, rollback,
hypothesis testing, reverse inference, trial & error, look inside, single
variable) to recommend the best debug approach.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


_RCA_METHODS = """
1. **分解法 (Divide & Conquer)** — Break the problem into smaller sub-problems.
   Isolate which part fails by testing each piece independently.

2. **对比法 (Comparison)** — Compare outcomes under different conditions.
   Run the same operation in a different environment, with different inputs,
   or compare a working vs broken path to spot the difference.

3. **回退法 (Rollback)** — Revert to a known-good state and re-apply changes
   incrementally until the failure reappears, identifying the breaking change.

4. **假设法 (Hypothesis Testing)** — Propose a specific cause and verify.
   "If the cause is X, then doing Y should produce Z." Test the prediction.

5. **逆推法 (Reverse Inference)** — Start from the error/output and trace
   backward through the call chain to locate the origin point.

6. **尝试法 (Trial & Error)** — Iterate on plausible fixes when the cause is
   unclear but the space of possibilities is small enough to enumerate.

7. **透视法 (Look Inside)** — Examine internal state: logs, debug output,
   intermediate values, data structures at the point of failure.

8. **单变量法 (Single Variable)** — Change exactly one thing at a time while
   keeping everything else constant, narrowing down the responsible factor.
"""


@tool_parameters(
    build_parameters_schema(
        error_description=p("string", "Optional — specific error or symptom to focus the analysis on. If empty, the tool reads the full conversation to infer what matters."),
        focus_method=p("string", "Optional — constrain analysis to one specific method: 'divide_conquer', 'comparison', 'rollback', 'hypothesis_testing', 'reverse_inference', 'trial_error', 'look_inside', 'single_variable'."),
        required=[],
    )
)
class DebugRootCauseTool(Tool):
    """Analyse conversation history and recommend a root-cause investigation direction."""

    name = "debug_root_cause_tool"
    description = (
        "**Purpose**: When tools failed repeatedly and the error is unclear, this tool reads "
        "the full conversation and applies 8 root-cause-analysis methods (divide & conquer, "
        "comparison, rollback, hypothesis testing, reverse inference, trial & error, look "
        "inside, single variable) to recommend the best investigation method + directions "
        "to examine. The agent then decides which tools to use.\n\n"
        "**When to call — triggered by the think-framework in these situations**:\n"
        "- **断链 (chain break)** — any processing stage (definition, premise "
        "verification, reasoning, validation) reached an impasse after standard "
        "recovery\n"
        "- **验证不通过 (validation failed)** — execution result did not match "
        "expectations in stage 4\n"
        "- **扩展认知 (extended cognition)** — the same tool failed 2+ times and "
        "you need a systematic investigation direction\n\n"
        "**How it differs from other debug tools**:\n"
        "- `diagnose_tool` searches code + git history for matching error text\n"
        "- `debug_root_cause_tool` reads the *full conversation* and recommends *which investigation method* + *what to examine* — the agent then chooses the right tools\n\n"
        "**How it differs from `assess_me_tool`**:\n"
        "- `assess_me_tool` audits what you know vs assume (cognition audit)\n"
        "- `debug_root_cause_tool` applies structured RCA methods to recommend a concrete debug direction"
    )

    read_only = True

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop
        self._session_key: ContextVar[str] = ContextVar(
            "debug_root_cause_session_key", default=""
        )

    def set_context(self, session_key: str) -> None:
        """Set the session key for reading conversation history."""
        self._session_key.set(session_key)

    async def execute(
        self,
        error_description: str = "",
        focus_method: str = "",
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

        from nanobot.agent.assess_me import format_conversation

        conversation = format_conversation(history)

        lines = [
            "You are a root-cause analysis expert. Your task is to read the conversation below "
            "and recommend the most effective investigation method.",
            "",
            "## Available Methods",
            _RCA_METHODS.strip(),
            "",
            "## Output Format",
            "Return two sections in your response:",
            "",
            "**Recommended method**: 1-2 methods from the list above and why they fit.",
            "",
            "**Investigation directions**: specific things to look for, comparisons to "
            "make, hypotheses to test, or state to examine. Be concrete — what exactly "
            "should the agent examine?",
            "",
            "## Important",
            "- Do NOT suggest specific tool calls (grep, read, exec, etc.) — the agent "
            "will decide which tools to use based on your direction",
            "- Focus on WHAT to investigate, not HOW to investigate it",
        ]

        if error_description:
            lines += [
                "",
                "## Specific Error / Symptom",
                error_description,
            ]

        if focus_method:
            lines += [
                "",
                "## Constrain To Method",
                focus_method,
            ]

        lines += [
            "",
            "## Conversation",
            conversation,
        ]

        prompt = "\n".join(lines)

        try:
            resp = await loop.provider.chat_stream(
                [{"role": "user", "content": prompt}],
                model=loop.model,
            )
        except Exception as e:
            logger.warning("debug_root_cause LLM call failed: {}", e)
            return f"Error: LLM call failed — {e}"

        return (resp.content or "").strip() or "(empty response)"
