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
        self._last_inject_iteration = -1

    async def before_iteration(self, context: AgentHookContext) -> None:
        # Only inject once per iteration (in case multiple hooks call this)
        if context.iteration == self._last_inject_iteration:
            return

        try:
            insight = self._build_insight(context)
            if insight:
                self._inject_insight(context, insight)
                self._last_inject_iteration = context.iteration
        except Exception:
            logger.debug("SelfInsightHook.before_iteration failed")

    def _build_insight(self, context: AgentHookContext) -> str | None:
        """Analyze logs and return an insight string, or None if nothing significant."""
        entries = self._load_jsonl(200)
        if not entries:
            return None

        warnings: list[str] = []

        # 1. Token growth rate
        if len(entries) >= 10:
            recent = entries[-20:]
            prompt_tokens = [e.get("prompt_tokens", 0) for e in recent]
            if prompt_tokens[0] > 0 and prompt_tokens[-1] > prompt_tokens[0]:
                growth = (prompt_tokens[-1] - prompt_tokens[0]) // max(len(recent) - 1, 1)
                if growth > TOKEN_GROWTH_THRESHOLD:
                    warnings.append(
                        f"⚠️ 上下文增长率 {growth} tokens/轮，注意压缩历史"
                    )

        # 2. Error cluster detection
        error_count = sum(1 for e in entries[-50:] if e.get("error_count", 0) > 0)
        if error_count >= ERROR_COUNT_THRESHOLD:
            warnings.append(f"⚠️ 近期 {error_count} 次错误，注意工具调用准确性")

        # 3. Repeated file edits (from tool_calls embedded in entries)
        edit_counts: dict[str, int] = defaultdict(int)
        for e in entries:
            for tc in e.get("tool_calls", []) or []:
                if tc.get("name") == "edit_file":
                    args = tc.get("arguments") or {}
                    path = args.get("file_path") or args.get("path") or ""
                    if path:
                        edit_counts[path] += 1

        for path, count in edit_counts.items():
            if count >= REPEAT_EDIT_THRESHOLD:
                short = path.split("/")[-1]
                warnings.append(f"⚠️ {short} 被编辑 {count} 次，注意避免循环修改")

        # 4. Discomfort themes
        discomfort: dict[str, int] = defaultdict(int)
        for e in entries:
            for sig in e.get("discomfort_signals", []):
                discomfort[sig] += 1
        top_discomfort = sorted(discomfort.items(), key=lambda x: -x[1])[:2]
        if top_discomfort and top_discomfort[0][1] >= 5:
            themes = ", ".join(k for k, _ in top_discomfort)
            warnings.append(f"⚠️ 高频不适信号: {themes}")

        if not warnings:
            return None

        # Truncate to avoid context bloat
        full = " | ".join(warnings)
        if len(full) > MAX_INSIGHT_CHARS:
            full = full[:MAX_INSIGHT_CHARS] + "…"
        return full

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
        reminder = {
            "role": "user",
            "content": (
                f"[Self-Insight from your history]\n{insight}\n"
                "— This is a reminder from your self-review system."
            ),
            "_source": "self_insight_hook",
            "_iteration": context.iteration,
        }
        # Inject as second message (after any existing system message)
        if context.messages and context.messages[0].get("role") == "system":
            # Insert after first system message
            context.messages.insert(1, reminder)
        else:
            context.messages.insert(0, reminder)