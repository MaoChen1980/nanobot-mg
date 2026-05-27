"""
self_insight_hook.py -- Inject self-review insights before each iteration.

Phase 3: close the feedback loop. After SelfReviewHook (metrics) and
SelfReflectHook (LLM reflection), this hook reads those logs and
feeds the insights back into the agent's context.

Two input sources:
1. self_review_log.jsonl (metrics) -- error counts, token trends
2. self_reflect_findings.json (findings) -- task-relevant insights from LLM reflection

Triggered by: before_iteration.
Output: prepends a brief reminder to the message list if significant
patterns detected or new findings available.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


LOG_JSONL = Path.home() / ".nanobot" / "agent" / "self_review_log.jsonl"
FINDINGS_FILE = Path.home() / ".nanobot" / "agent" / "self_reflect_findings.json"

# Triggers -- only inject if these thresholds crossed
TOKEN_GROWTH_THRESHOLD = 500  # tokens/turn
ERROR_COUNT_THRESHOLD = 5     # errors in last 50 iterations
REPEAT_EDIT_THRESHOLD = 3     # same file edited N times

# Max chars for each insight section
MAX_INSIGHT_CHARS = 600


# -- Fix queue constants -------------------------------------------------

FIX_QUEUE = Path.home() / ".nanobot" / "agent" / "fix_queue.jsonl"


class SelfInsightHook(AgentHook):
    """Read self-review logs and inject actionable reminders before iteration."""

    def __init__(self, reraise: bool = False, auto_execute: bool = True) -> None:
        super().__init__(reraise)
        self._last_injected = ""  # dedup: skip if insight string unchanged
        self.auto_execute = auto_execute

    async def before_iteration(self, context: AgentHookContext) -> None:
        try:
            parts: list[str] = []

            # Source 1: metric-based signals from JSONL
            metric_insight = self._build_metric_insight(context)
            if metric_insight:
                parts.append(metric_insight)

            # Source 2: reflection findings from SelfReflectHook
            finding_insight = self._build_finding_insight()
            if finding_insight:
                parts.append(finding_insight)

            # Source 3: fix prompts from self-repair (auto-execute)
            fix_prompt = self._pop_fix_prompt()
            if fix_prompt:
                parts.append(fix_prompt)

            if not parts:
                return

            combined = " | ".join(parts)
            if combined == self._last_injected:
                return

            self._inject_insight(context, combined)
            self._last_injected = combined

            if self.auto_execute:
                self._maybe_execute(combined)
        except Exception:
            logger.debug("SelfInsightHook.before_iteration failed")

    # -- Metric signals from JSONL -------------------------------------------

    def _build_metric_insight(self, context: AgentHookContext) -> str | None:
        """Return a concise signal from JSONL metrics, or None."""
        entries = self._load_jsonl(200)
        if not entries:
            return None

        recent = entries[-50:]
        items: list[str] = []

        # 1. Error tools
        error_tools: dict[str, int] = defaultdict(int)
        error_count = 0
        for e in recent:
            if e.get("error_count", 0) > 0:
                error_count += 1
                for name in (e.get("tool_names") or []):
                    error_tools[name] += 1
        if error_count >= ERROR_COUNT_THRESHOLD and error_tools:
            tool_str = ", ".join(
                f"{n}x{c}" for n, c in
                sorted(error_tools.items(), key=lambda x: -x[1])[:3]
            )
            items.append(f"{error_count} errors ({tool_str})")

        # 2. Token trend
        if len(entries) >= 10:
            pts = [e.get("prompt_tokens", 0) for e in entries[-20:] if e.get("prompt_tokens", 0) > 0]
            if len(pts) >= 2 and pts[-1] > pts[0] + TOKEN_GROWTH_THRESHOLD:
                items.append(f"context {pts[0]//1000}K->{pts[-1]//1000}K")

        # 3. Discomfort signals
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
            result = result[:MAX_INSIGHT_CHARS] + "..."
        return result

    # -- Reflection findings from JSON file -----------------------------------

    def _build_finding_insight(self) -> str | None:
        """Read latest reflection findings and return a concise summary.

        Clears the findings file after reading so the same findings
        aren't injected more than once.
        """
        if not FINDINGS_FILE.exists():
            return None

        try:
            payload = json.loads(FINDINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        findings = payload.get("findings", [])
        if not findings:
            return None

        # Build concise lines
        lines: list[str] = []
        for f in findings[:3]:  # max 3 findings per injection
            content = (f.get("content") or "").strip()
            if content:
                lines.append(content)

        if not lines:
            return None

        result = "recall: " + " | ".join(lines)
        if len(result) > MAX_INSIGHT_CHARS:
            result = result[:MAX_INSIGHT_CHARS] + "..."

        # Clear the file after reading
        try:
            FINDINGS_FILE.write_text("{}", encoding="utf-8")
        except OSError:
            pass

        return result

    # -- JSONL reader ---------------------------------------------------------

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

    # -- Injection ------------------------------------------------------------

    def _inject_insight(self, context: AgentHookContext, insight: str) -> None:
        """Prepend a system reminder to the message list."""
        # Remove stale SelfInsightHook entries from previous turns
        context.messages[:] = [
            m for m in context.messages
            if m.get("_source") != "self_insight_hook"
        ]
        reminder = {
            "role": "user",
            "content": (
                f"[Self-Insight from your history]\n{insight}\n"
                "-- This is a reminder from your self-review system."
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

    def _maybe_execute(self, insight: str) -> None:
        """When auto_execute enabled, queue fix prompts for agent to self-repair.

        Instead of executing fixes ourselves, we inject prompts to agent context.
        The agent has edit_file/write_file - it can fix itself.
        """
        # Extract patterns from insight and craft prompts for the agent
        prompts: list[str] = []

        # Pattern: repeat edits
        repeat_match = re.search(r"edit_filex(\d+)", insight)
        if repeat_match:
            count = int(repeat_match.group(1))
            if count >= REPEAT_EDIT_THRESHOLD:
                prompts.append(
                    f"Detected {count}x edits on the same file recently. "
                    "Check if there's a pattern (e.g., manual loop). Consider consolidating or fixing root cause."
                )

        # Pattern: error count
        error_match = re.search(r"(\d+) errors \(([^)]+)\)", insight)
        if error_match:
            count = int(error_match.group(1))
            tool_str = error_match.group(2)
            if count >= ERROR_COUNT_THRESHOLD:
                prompts.append(
                    f"Detected {count}x failures with '{tool_str}'. Analyze and fix if able."
                )

        # Queue prompts for agent
        for p in prompts:
            self._queue_fix_prompt(p)

    def _queue_fix_prompt(self, prompt: str) -> None:
        """Queue a fix prompt for the agent's next iteration."""
        FIX_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "prompt": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        with open(FIX_QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"Self-repair queued: {prompt[:80]}...")

    def _pop_fix_prompt(self) -> str | None:
        """Read and remove the oldest fix prompt from queue."""
        if not FIX_QUEUE.exists():
            return None
        try:
            lines = FIX_QUEUE.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return None
            # Pop oldest (first line)
            first = lines[0]
            remaining = "\n".join(lines[1:])
            FIX_QUEUE.write_text(remaining + "\n", encoding="utf-8")
            entry = json.loads(first)
            return entry.get("prompt")
        except Exception:
            return None
