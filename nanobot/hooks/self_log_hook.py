"""
SelfLogHook: lightweight per-iteration metrics logger.

Phase 1: minimal data capture — logs metrics to self_review_log.jsonl.
Phase 2 (SelfDetectHook) adds LLM suspect detection.
Phase 3 (SelfFixHook) injects findings back into agent context.

Together they form the self-evolution feedback loop:
  SelfLogHook (log) → SelfDetectHook (detect) → SelfFixHook (fix)

Log file: ~/.nanobot/self_improve/self_review_log.jsonl
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


class SelfLogHook(AgentHook):
    """Lightweight metrics logger that runs after each iteration.

    Captures: tool call counts, errors, empty results, usage stats.
    No LLM calls — all captured from hook context.
    Phase 2 (SelfDetectHook) and Phase 3 (SelfFixHook) complete the loop.
    """

    LOG_FILE = Path.home() / ".nanobot" / "self_improve" / "self_review_log.jsonl"

    # Patterns that count as "discomfort" signals
    DISCOMFORT_PATTERNS = [
        "error",
        "failed",
        "not found",
        "permission denied",
        "timeout",
        "empty result",
        "no such file",
        "does not exist",
    ]

    async def after_iteration(self, context: AgentHookContext) -> None:
        try:
            self._capture(context)
        except Exception:
            logger.debug("SelfLogHook.after_iteration failed silently")

    def _capture(self, context: AgentHookContext) -> None:
        # Build basic metrics from context
        tool_count = len(context.tool_calls)
        error_count = sum(
            1
            for r in (context.tool_results or [])
            if self._is_error_result(r)
        )
        empty_result_count = sum(
            1
            for r in (context.tool_results or [])
            if self._is_empty_result(r)
        )

        # Count discomfort signals in tool results
        discomfort_signals = []
        for r in context.tool_results or []:
            signal = self._detect_discomfort(r)
            if signal:
                discomfort_signals.append(signal)

        # Basic usage stats
        usage = context.usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iteration": context.iteration,
            "tool_count": tool_count,
            "tool_names": [tc.name for tc in (context.tool_calls or [])],
            "error_count": error_count,
            "empty_result_count": empty_result_count,
            "discomfort_signals": discomfort_signals,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "has_error": context.error is not None,
            "has_final_content": context.final_content is not None,
            "message_count": len(context.messages),
        }

        self._append_log(entry)

    def _is_error_result(self, result: object) -> bool:
        """Check if a tool result looks like an error."""
        if result is None:
            return False
        s = str(result).lower()
        return any(p in s for p in ["error", "exception", "failed", "timeout"])

    # ⚠️ NOTE: `_is_empty_result` checks `str(result).strip()` against literal
    # strings like `""` and `"[]"`. When `result` is a dict like `{"result": ""}`,
    # `str(dict)` produces `"{'result': ''}"` — NOT an empty string.
    # The discomfort-signal detection (`_detect_discomfort`) is the meaningful
    # signal here; `_is_empty_result` is secondary.
    def _is_empty_result(self, result: object) -> bool:
        """Check if a tool result is empty or null."""
        if result is None:
            return True
        s = str(result).strip()
        return s in ("", "None", "[]", "{}", "null")

    def _detect_discomfort(self, result: object) -> str | None:
        """Detect discomfort signals in tool results."""
        if result is None:
            return None
        s = str(result).lower()
        for pattern in self.DISCOMFORT_PATTERNS:
            if pattern in s:
                return pattern
        return None

    def _append_log(self, entry: dict) -> None:
        """Append one JSON line to the log file."""
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

