"""
self_insight_hook.py -- Inject self-reflection findings into agent context.

Phase 3: close the feedback loop. After SelfReflectHook produces findings
(self_bug, correction, behavior, etc.), this hook reads them and injects
unreported, unresolved findings as [Self-Insight] user messages.

Triggered by: before_iteration.

The hook does NOT execute or decide anything — it only reminds.
The LLM decides whether to fix, mark as resolved, and restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


FINDINGS_FILE = Path.home() / ".nanobot" / "agent" / "self_reflect_findings.json"
RESOLVED_FILE = Path.home() / ".nanobot" / "agent" / "resolved_findings.jsonl"

MAX_INSIGHT_CHARS = 600


def _read_resolved_ids() -> set[str]:
    """Read resolved finding IDs from JSONL file (one ID per line)."""
    if not RESOLVED_FILE.exists():
        return set()
    ids: set[str] = set()
    try:
        for line in RESOLVED_FILE.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if line:
                ids.add(line)
    except OSError:
        pass
    return ids


class SelfInsightHook(AgentHook):
    """Inject self-reflection findings into context for LLM to act on.

    Only reminds — the LLM decides whether to fix, resolve, or ignore.
    Dedup: same finding is only injected once per session.
    Resolution: LLM writes finding ID to resolved_findings.jsonl to suppress future injections.
    """

    def __init__(self, reraise: bool = False) -> None:
        super().__init__(reraise)
        self._reported_ids: set[str] = set()  # finding IDs already injected this session
        self._last_injected = ""  # dedup: skip if insight string unchanged

    async def before_iteration(self, context: AgentHookContext) -> None:
        try:
            finding_insight = self._build_finding_insight()
            if not finding_insight:
                return

            if finding_insight == self._last_injected:
                return

            self._inject_insight(context, finding_insight)
            self._last_injected = finding_insight
        except Exception:
            logger.debug("SelfInsightHook.before_iteration failed")

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
        resolved = _read_resolved_ids()

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
            "\n\n[Note] 以上全部是可疑点——不一定是问题。"
            "判断前先感知三个维度："
            "{人} 用户是谁、习惯偏好、技术水平；"
            "{环境} CPU/内存/磁盘、部署规模；"
            "{行为} 你自己的操作模式、重复错误、走过的弯路。"
            "三者结合再决定：哪些值得改、哪些是刻意的、怎么改。"
            "修复后标记已解决: echo '<id>' >> ~/.nanobot/agent/resolved_findings.jsonl"
        )
        if len(result) > MAX_INSIGHT_CHARS:
            result = result[:MAX_INSIGHT_CHARS] + "..."

        return result

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
