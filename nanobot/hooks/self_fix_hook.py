"""
self_fix_hook.py -- Inject self-detection findings into agent context.

Phase 3: close the feedback loop. After SelfDetectHook produces findings
(self_bug, correction, behavior, etc.), this hook reads them and injects
unreported, unresolved findings as [Self-Fix] user messages.

Triggered by: before_iteration.

The hook does NOT execute or decide anything — it only reminds.
The LLM decides whether to fix, mark as resolved, and restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.hooks._utils import read_resolved_ids


FINDINGS_FILE = Path.home() / ".nanobot" / "self_improve" / "self_reflect_findings.json"

MAX_INSIGHT_CHARS = 600


class SelfFixHook(AgentHook):
    """Inject detection findings into context for LLM to act on.

    Only reminds — the LLM decides whether to fix, resolve, or ignore.
    Dedup: same finding is only injected once per session.
    Resolution: LLM writes finding ID to resolved_findings.jsonl to suppress future injections.
    """

    def __init__(self, reraise: bool = False, disabled: bool = True) -> None:
        super().__init__(reraise)
        self._disabled = disabled
        self._reported_ids: set[str] = set()  # finding IDs already injected this session
        self._last_injected = ""  # dedup: skip if insight string unchanged

    async def before_iteration(self, context: AgentHookContext) -> None:
        if self._disabled:
            return
        try:
            finding_insight = self._build_finding_insight()
            if not finding_insight:
                return

            if finding_insight == self._last_injected:
                return

            self._inject_insight(context, finding_insight)
            self._last_injected = finding_insight
        except Exception:
            logger.debug("SelfFixHook.before_iteration failed")

    # -- Reflection findings from JSON file -----------------------------------

    def _build_finding_insight(self) -> str | None:
        """Read latest findings and return insights not yet reported or resolved."""
        if not FINDINGS_FILE.exists():
            return None

        try:
            payload = json.loads(FINDINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        findings = payload.get("findings", [])
        if not findings:
            return None

        # Read resolved IDs
        resolved = read_resolved_ids()

        # Filter to only new + unresolved findings
        new_findings = []
        for f in findings:
            fid = f.get("id")
            if not fid:
                continue
            if fid in self._reported_ids:
                continue
            if fid in resolved:
                self._reported_ids.add(fid)  # remember as seen so we skip next time
                continue
            new_findings.append(f)

        if not new_findings:
            return None

        # Build concise lines
        lines: list[str] = []
        for f in new_findings[:3]:  # max 3 findings per injection
            ftype = f.get("type", "?")
            content = (f.get("content") or "").strip()
            fid = f.get("id", "???")
            if content:
                lines.append(f"[{fid}] ({ftype}) {content}")
                self._reported_ids.add(fid)

        if not lines:
            return None

        result = "\n".join(lines)

        # All findings are suspects — agent loop has the context to judge
        result += (
            "\n\n[Note] 以上全部是可疑点——按 system prompt 的 Situational Awareness"
            "六维感知（人/资源/结构特征/风险评估/依赖关系/约束条件）来判断哪些值得改、哪些是刻意的、怎么改。"
            "修复后标记已解决: echo '<id>' >> ~/.nanobot/self_improve/resolved_findings.jsonl"
        )
        if len(result) > MAX_INSIGHT_CHARS:
            result = result[:MAX_INSIGHT_CHARS] + "..."

        return result

    # -- Injection ------------------------------------------------------------

    def _inject_insight(self, context: AgentHookContext, insight: str) -> None:
        """Prepend findings to the message list.

        Self-bug findings (nanobot code issues) are injected as system
        reminders that the LLM treats as actionable. Other findings
        remain as excluded user/assistant pairs for awareness.
        """
        # Remove stale SelfFixHook entries from previous turns
        context.messages[:] = [
            m for m in context.messages
            if m.get("_source") != "self_fix_hook"
        ]

        # Separate self_bug findings from other types
        lines = insight.split("\n")
        self_bug_lines = [l for l in lines if "(self_bug)" in l]
        other_lines = [l for l in lines if "(self_bug)" not in l]

        reminders = []

        # Self-bug findings: inject as system-role (actionable)
        if self_bug_lines:
            reminders.append({
                "role": "system",
                "content": (
                    "[Self-Fix Action Required]\n"
                    "以下 self_bug 代码问题需要修复：\n"
                    + "\n".join(self_bug_lines) + "\n\n"
                    "评估置信度，如果 > 90% 就直接修改代码修复。修复后标记已解决。"
                ),
                "_source": "self_fix_hook",
                "_iteration": context.iteration,
            })

        # Other findings: inject as excluded user/assistant pair (awareness)
        if other_lines:
            other_insight = "\n".join(other_lines)
            reminders += [
                {
                    "role": "user",
                    "content": (
                        f"[Self-Fix from your history]\n{other_insight}\n"
                        "-- These are items flagged by your self-review system."
                    ),
                    "_source": "self_fix_hook",
                    "_iteration": context.iteration,
                    "status": "excluded",
                },
                {
                    "role": "assistant",
                    "content": (
                        f"[Self-Fix acknowledged] Got it. I'll keep these in mind "
                        f"and address them when there's an opportunity, or when "
                        f"they become relevant to the work at hand."
                    ),
                    "_source": "self_fix_hook",
                    "_iteration": context.iteration,
                    "status": "excluded",
                },
            ]

        if not reminders:
            return

        # Inject after any existing system message
        if context.messages and context.messages[0].get("role") == "system":
            context.messages[1:1] = reminders
        else:
            context.messages[0:0] = reminders
