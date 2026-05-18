"""Result verification for subtask outputs.

Uses an independent Agent with read-only tools to verify subtask
results against goal-defined success criteria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider
from nanobot.utils.prompt_templates import render_template


@dataclass
class VerifierResult:
    """Outcome of a result verification."""
    passed: bool
    evidence: list[str] = field(default_factory=list)
    details: str = ""


class VerifierAgent:
    """Verifies subtask outputs by running an independent read-only Agent.

    The verification agent:
    1. Reads the goal's success_criteria from scope
    2. Gets a filtered set of read-only tools (read_file, grep, glob, list_dir)
    3. Renders the verify_result.md prompt template
    4. Runs AgentRunner to check each criterion using tools
    5. Returns passed/failed with evidence
    """

    _READ_ONLY_TOOL_NAMES = frozenset({
        "read_file", "grep", "glob", "list_dir",
    })

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        model: str,
        max_iterations: int = 10,
        max_tool_result_chars: int = 8000,
    ):
        self._provider = provider
        self._tools = tools
        self._model = model
        self._max_iterations = max_iterations
        self._max_tool_result_chars = max_tool_result_chars
        self._runner = AgentRunner(provider)

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Hot-swap the provider — recreates runner so new provider state is used."""
        self._provider = provider
        self._model = model
        self._runner = AgentRunner(provider)

    def _build_readonly_registry(self) -> ToolRegistry:
        """Build a ToolRegistry with only read-only tools."""
        reg = ToolRegistry()
        for name, tool in self._tools._tools.items():
            if getattr(tool, "read_only", False) and name in self._READ_ONLY_TOOL_NAMES:
                reg.register(tool)
        return reg

    async def verify(
        self,
        goal: dict[str, Any],
        subtask: dict[str, Any],
        final_content: str | None,
        tools_used: list[str],
    ) -> VerifierResult:
        """Verify a subtask's output against the goal's success criteria.

        Args:
            goal: The goal dict (must have scope.structural_constraints.success_criteria)
            subtask: The subtask dict that was executed
            final_content: The final output from the subtask execution
            tools_used: List of tool names used during execution

        Returns:
            VerifierResult with passed/failed verdict and evidence
        """
        scope = goal.get("scope", {})
        constraints = scope.get("structural_constraints", {})
        success_criteria = constraints.get("success_criteria", [])

        if not success_criteria:
            return VerifierResult(
                passed=True,
                evidence=[],
                details="No success criteria defined — skipping verification.",
            )

        subtask_id = subtask.get("id", "?")
        subtask_title = subtask.get("title", subtask_id)

        # Build initial messages from template
        system_msg = render_template(
            "agent/verify_result.md",
            part="system",
            success_criteria=success_criteria,
            subtask_id=subtask_id,
            subtask_title=subtask_title,
            final_content=final_content or "",
            tools_used=tools_used,
        )
        user_msg = render_template(
            "agent/verify_result.md",
            part="user",
            subtask_id=subtask_id,
            subtask_title=subtask_title,
        )

        readonly_registry = self._build_readonly_registry()

        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            tools=readonly_registry,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=self._max_tool_result_chars,
        )

        logger.info(
            "Verifying subtask {}/{} | criteria={}",
            subtask_id, subtask_title, success_criteria,
        )

        result = await self._runner.run(spec)

        # Determine verdict from the result
        passed = self._parse_verdict(result.final_content or "")

        evidence = self._extract_evidence(result.messages)

        logger.info(
            "Verification result for {}: passed={}",
            subtask_id, passed,
        )

        return VerifierResult(
            passed=passed,
            evidence=evidence,
            details=result.final_content or "",
        )

    def _parse_verdict(self, content: str) -> bool:
        """Parse the verification verdict from LLM output."""
        content_lower = content.lower()
        if "verdict: passed" in content_lower or "all criteria met" in content_lower:
            return True
        if "verdict: failed" in content_lower or "criterion not met" in content_lower:
            return False
        if "failed" in content_lower and "passed" not in content_lower:
            return False
        return True

    def _extract_evidence(self, messages: list[dict[str, Any]]) -> list[str]:
        """Extract evidence lines from assistant messages."""
        evidence = []
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    lines = [l.strip() for l in content.split("\n") if l.strip()]
                    evidence.extend(lines[:5])
        return evidence[:20]
