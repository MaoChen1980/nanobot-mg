"""
self_insight_hook.py — Inject self-review insights before each iteration.

Phase 3: close the feedback loop. After SelfReviewHook (metrics) and
SelfReflectHook (LLM reflection), this hook reads those logs and
feeds the insights back into the agent's context.

Triggered by: before_iteration, reads self_log.md + self_review_log.jsonl.
Output: prepends a brief reminder to the message list if significant
patterns detected (repeated edits, token growth, error clusters).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


LOG_JSONL = Path.home() / ".nanobot" / "agent" / "self_review_log.jsonl"
LOG_MD = Path.home() / ".nanobot" / "agent" / "self_log.md"

# Triggers — only inject if these thresholds crossed
TOKEN_GROWTH_THRESHOLD = 500  # tokens/turn
ERROR_COUNT_THRESHOLD = 5     # errors in last 50 iterations
REPEAT_EDIT_THRESHOLD = 3     # same file edited N times

# Max history to keep in prompt (avoid bloating context further)
MAX_INSIGHT_CHARS = 600


class SelfInsightHook(AgentHook):
    """Read self-review logs and inject actionable reminders before iteration."""

    def __init__(self, reraise: bool = False) -> None:
        super().__init__(reraise)
        self._last_injected = ""  # dedup: skip if insight string unchanged

    async def before_iteration(self, context: AgentHookContext) -> None:
        try:
            insight = self._build_insight(context)
            if insight and insight != self._last_injected:
                self._inject_insight(context, insight)
                self._last_injected = insight
        except Exception:
            logger.debug("SelfInsightHook.before_iteration failed")

    def _build_insight(self, context: AgentHookContext) -> str | None:
        """Return a concise signal for the LLM to investigate, or None."""
        entries = self._load_jsonl(200)
        if not entries:
            return None

        recent = entries[-50:]
        items: list[str] = []

        # 1. Error tools — which tools had errors recently
        error_tools: dict[str, int] = defaultdict(int)
        error_count = 0
        for e in recent:
            if e.get("error_count", 0) > 0:
                error_count += 1
                for name in (e.get("tool_names") or []):
                    error_tools[name] += 1
        if error_count >= ERROR_COUNT_THRESHOLD and error_tools:
            tool_str = ", ".join(
                f"{n}×{c}" for n, c in
                sorted(error_tools.items(), key=lambda x: -x[1])[:3]
            )
            items.append(f"⚠️ {error_count}次错误({tool_str})")

        # 2. Token trend — concrete numbers
        if len(entries) >= 10:
            pts = [e.get("prompt_tokens", 0) for e in entries[-20:] if e.get("prompt_tokens", 0) > 0]
            if len(pts) >= 2 and pts[-1] > pts[0] + TOKEN_GROWTH_THRESHOLD:
                items.append(f"context {pts[0]//1000}K→{pts[-1]//1000}K")

        # 3. Discomfort signals — what went wrong
        discomfort: dict[str, int] = defaultdict(int)
        for e in recent:
            for sig in e.get("discomfort_signals", []):
                discomfort[sig] += 1
        top = sorted(discomfort.items(), key=lambda x: -x[1])[:2]
        if top and top[0][1] >= ERROR_COUNT_THRESHOLD:
            items.append("signal: " + ", ".join(f"{k}({c})" for k, c in top))

        if not items:
            return None

        result = " | ".join(items)
        if len(result) > MAX_INSIGHT_CHARS:
            result = result[:MAX_INSIGHT_CHARS] + "…"
        return result

    def _load_jsonl(self, max_lines: int = 200) -> list[dict[str, Any]]:
        if not LOG_JSONL.exists():
            return []
        lines = []
        with open(LOG_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        pass
                if len(lines) >= max_lines:
                    break
        return lines

    def _inject_insight(self, context: AgentHookContext, insight: str) -> None:
        """Prepend a system reminder to the message list."""
        # Remove stale SelfInsightHook entries from previous turns so they
        # don't accumulate in session history and bloat context.
        context.messages[:] = [
            m for m in context.messages
            if m.get("_source") != "self_insight_hook"
        ]
        reminder = {
            "role": "user",
            "content": (
                f"[Self-Insight from your history]\n{insight}\n"
                "— This is a reminder from your self-review system."
            ),
            "_source": "self_insight_hook",
            "_iteration": context.iteration,
            "status": "excluded",
        }
        # Inject as second message (after any existing system message)
        if context.messages and context.messages[0].get("role") == "system":
            context.messages.insert(1, reminder)
        else:
            context.messages.insert(0, reminder)