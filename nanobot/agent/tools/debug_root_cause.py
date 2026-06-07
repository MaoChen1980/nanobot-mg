"""Debug root-cause tool — read conversation history and suggest investigation direction.

When tools fail repeatedly, this tool reads the full conversation and applies
8 root-cause-analysis methods (divide & conquer, comparison, rollback,
hypothesis testing, reverse inference, trial & error, look inside, single
variable) to recommend the best debug approach.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from loguru import logger

from nanobot.agent.llm_context import chat
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


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
        problem=p("string", "The specific problem, error, or symptom to debug. Describe it clearly — what happened, what was expected, and any relevant context."),
        focus_method=p("string", "Optional — constrain analysis to one specific method: 'divide_conquer', 'comparison', 'rollback', 'hypothesis_testing', 'reverse_inference', 'trial_error', 'look_inside', 'single_variable'."),
        required=["problem"],
    )
)
class DebugRootCauseTool(Tool):
    """Analyse conversation history and recommend a root-cause investigation direction."""

    name = "debug_root_cause_tool"
    description = (
        "**Purpose**: You describe a problem you're stuck on, and this tool returns "
        "a structured debug plan: recommended investigation method(s) + specific "
        "directions to examine. It reads the full conversation for context, so your "
        "problem description can be brief — the tool already has the background.\n\n"
        "**When to call — you are in one of these situations**:\n"
        "- You tried a few approaches but keep getting different errors, no clear pattern\n"
        "- You don't know where to start investigating — the problem space feels too large\n"
        "- A tool failed 2+ times and retrying the same thing won't help\n"
        "- The error is intermittent or non-deterministic and you need a systematic strategy\n"
        "- You need to step back and choose an investigation method instead of guessing\n\n"
        "**Output**: Recommended method(s) from 8 RCA approaches (divide & conquer, "
        "comparison, rollback, hypothesis testing, reverse inference, trial & error, "
        "look inside, single variable) + concrete things to examine. You decide which "
        "tools to use for the actual investigation.\n\n"
        "**How it differs from other tools**:\n"
        "- `diagnose_tool` searches code + git history for matching error text\n"
        "- `assess_me_tool` audits what you know vs assume (cognition audit)\n"
        "- `reframe_tool` re-states the problem cleanly for a fresh perspective\n"
        "- `debug_root_cause_tool` gives you a **systematic investigation strategy** — "
        "which method to use and what to look for"
    )

    read_only = True

    def __init__(self) -> None:
        self._messages: ContextVar[list[dict[str, Any]]] = ContextVar(
            "debug_root_cause_messages", default=[]
        )

    def set_context(self, messages: list[dict[str, Any]]) -> None:
        """Set the conversation messages for analysis."""
        self._messages.set(messages)

    async def execute(
        self,
        problem: str,
        focus_method: str = "",
        **kwargs: Any,
    ) -> str:
        messages = self._messages.get()
        if not messages:
            return "Error: no active session — cannot read conversation history."

        from nanobot.agent.assess_me import format_conversation

        conversation = format_conversation(messages)

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
            "**Recommended methods**: List applicable methods ordered by recommendation priority "
            "(most effective first). You may also suggest investigation methods beyond the 8 listed "
            "above if they better fit the problem — describe what they are and why they apply.",
            "",
            "**Investigation directions**: For each recommended method, give specific things to "
            "look for, comparisons to make, hypotheses to test, or state to examine. Be concrete — "
            "what exactly should the agent examine?",
            "",
            "## Important",
            "- Do NOT suggest specific tool calls (grep, read, exec, etc.) — the agent "
            "will decide which tools to use based on your direction",
            "- Focus on WHAT to investigate, not HOW to investigate it",
        ]

        if problem:
            lines += [
                "",
                "## Problem to Debug",
                problem,
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
            resp = await chat(
                [{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.warning("debug_root_cause LLM call failed: {}", e)
            return f"Error: LLM call failed — {e}"

        return (resp.content or "").strip() or "(empty response)"
