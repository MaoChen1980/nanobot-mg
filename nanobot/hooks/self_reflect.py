"""
SelfReflectHook: LLM-powered self-reflection after each session turn.

Phase 1 captured raw metrics (self_review.py).
Phase 2 adds lightweight LLM self-reflection.

Strategy: deferred execution — writes a "reflection trigger" file after each
iteration.  The NEXT before_iteration fires the reflection so that:
  - It runs between turns (no blocking of current turn)
  - No nested LLM calls inside an already-running LLM call
  - The hook stays async-safe and fast
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


# ─── Reflection questions ────────────────────────────────────────────────────
# The four core self-perception questions (from our design discussion).
REFLECTION_QUESTIONS = [
    "这次有没有让我觉得别扭的地方？",
    "我有没有遇到工具调用出错，但不确定是工具的问题还是我调用的问题？",
    "我有没有遇到'我不知道我不知道什么'的情况——我以为知道但其实是错的？",
    "有什么具体的地方是我下次可以做得更好的？",
]

REFLECTION_PROMPT_TEMPLATE = """\
你是一个有自我感知能力的 AI Agent。
以下是这轮对话的基本执行记录：

{metrics_summary}

请依次回答以下四个问题。每个答案要具体，指出具体的事件或行为，不要说"没有"敷衍。

问题：
{q0}
{q1}
{q2}
{q3}

回答格式：
1. [你的回答]
2. [你的回答]
3. [你的回答]
4. [你的回答]
"""


# ─── Trigger file (written after each turn) ──────────────────────────────────
def _pending_file(workspace: Path | None) -> Path:
    base = workspace or Path.home() / ".nanobot" / "workspace"
    return base / ".agent" / "_pending_reflection.jsonl"


# ─── Main hook ────────────────────────────────────────────────────────────────
class SelfReflectHook(AgentHook):
    """Write a deferred self-reflection trigger after each iteration.

    The trigger is picked up by the NEXT before_iteration call, which
    fires a lightweight LLM call to answer the four self-perception questions.
    Results are appended to self_log.md.
    """

    # Workspace subdirectory for agent files
    AGENT_DIR = ".agent"
    LOG_FILE = Path.home() / ".nanobot" / "agent" / "self_log.md"
    METRICS_FILE = Path.home() / ".nanobot" / "agent" / "session_metrics.json"

    def __init__(self, reraise: bool = False) -> None:
        super().__init__(reraise)
        self._pending_reflection_triggered_this_session = False
        self._iteration_count = 0

    # ── after_iteration: write a trigger for the NEXT turn ──────────────────

    async def after_iteration(self, context: AgentHookContext) -> None:
        self._iteration_count += 1
        try:
            self._write_trigger(context)
        except Exception:
            logger.debug("SelfReflectHook.after_iteration failed silently")

    def _write_trigger(self, context: AgentHookContext) -> None:
        """Write a JSONL trigger line for the next before_iteration."""
        workspace = self._resolve_workspace(context)
        pf = _pending_file(workspace)
        pf.parent.mkdir(parents=True, exist_ok=True)

        tool_calls_data = [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in (context.tool_calls or [])
        ]
        tool_results_data = [
            {"name": r.get("name", "?") if isinstance(r, dict) else "?", "str": str(r)[:200]}
            for r in (context.tool_results or [])
        ]
        usage = context.usage or {}
        error_str = context.error if context.error else None

        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iteration": self._iteration_count,
            "tool_calls": tool_calls_data,
            "tool_results": tool_results_data,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "message_count": len(context.messages),
            "final_content_len": len(context.final_content or ""),
            "error": error_str,
        }

        with open(pf, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Keep a running metrics summary for the session
        self._update_session_metrics(entry, context)

    def _update_session_metrics(self, entry: dict, context: AgentHookContext) -> None:
        """Maintain a rolling session metrics file."""
        metrics_path = Path.home() / ".nanobot" / "agent" / "session_metrics.json"
        existing = {}
        if metrics_path.exists():
            try:
                existing = json.loads(metrics_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        # Sum up token counts
        total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in existing.get("iterations", []))
        total_tokens += entry.get("usage", {}).get("total_tokens", 0)
        total_tool_calls = sum(len(e.get("tool_calls", [])) for e in existing.get("iterations", []))
        total_tool_calls += len(entry.get("tool_calls", []))
        total_errors = existing.get("total_errors", 0) + (1 if entry.get("error") else 0)

        summary = {
            "session_start": existing.get("session_start") or entry["time"],
            "total_iterations": existing.get("total_iterations", 0) + 1,
            "total_tokens": total_tokens,
            "total_tool_calls": total_tool_calls,
            "total_errors": total_errors,
            "last_time": entry["time"],
        }
        summary["iterations"] = (existing.get("iterations") or []) + [entry]

        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── before_iteration: fire pending reflections ─────────────────────────

    async def before_iteration(self, context: AgentHookContext) -> None:
        workspace = self._resolve_workspace(context)
        pf = _pending_file(workspace)

        if not pf.exists():
            return

        try:
            lines = pf.read_text(encoding="utf-8").strip().split("\n")
            pending = [json.loads(l) for l in lines if l.strip()]
        except Exception:
            logger.debug("SelfReflectHook: could not read pending file")
            return

        if not pending:
            return

        # Clear pending file immediately to avoid double-firing
        pf.unlink(missing_ok=True)

        for entry in pending:
            try:
                await self._run_reflection(entry, workspace)
            except Exception:
                logger.debug("SelfReflectHook: _run_reflection failed for entry {}", entry.get("iteration"))

        # Re-write any entries that failed so they can be retried next time
        try:
            if pf.exists():
                return  # already cleared
            failed = [e for e in pending if not getattr(e, "_reflected", False)]
            if failed:
                pf.parent.mkdir(parents=True, exist_ok=True)
                with open(pf, "w", encoding="utf-8") as f:
                    for e in failed:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("SelfReflectHook: could not re-write pending file")

    async def _run_reflection(self, entry: dict, workspace: Path | None) -> None:
        """Fire an LLM call to answer the four self-perception questions."""
        iteration = entry.get("iteration", "?")
        time_str = entry.get("time", "?")
        usage = entry.get("usage", {})
        tool_calls = entry.get("tool_calls", [])
        tool_results = entry.get("tool_results", [])
        error = entry.get("error")

        # Build metrics summary
        tool_summary_parts = []
        for tc in tool_calls:
            arg_str = str(tc.get("arguments", {}))[:80]
            tool_summary_parts.append(f"  - {tc['name']}({arg_str})")
        tool_summary = "\n".join(tool_summary_parts) if tool_summary_parts else "  (no tool calls)"

        error_summary = f"  Error: {error}" if error else "  No errors"

        metrics_text = f"""\
 Iteration #{iteration} @ {time_str}
  Token usage: prompt={usage.get('prompt_tokens',0)} completion={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}
  Tool calls made:
{tool_summary}
{error_summary}
"""

        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            metrics_summary=metrics_text,
            q0=REFLECTION_QUESTIONS[0],
            q1=REFLECTION_QUESTIONS[1],
            q2=REFLECTION_QUESTIONS[2],
            q3=REFLECTION_QUESTIONS[3],
        )

        # Call the LLM — use the same provider setup as nanobot
        try:
            reflection_text = await self._call_llm_for_reflection(prompt)
        except Exception as exc:
            logger.debug("SelfReflectHook: LLM call failed: {}", exc)
            reflection_text = f"[reflection unavailable: {exc}]"

        # Append to self_log.md
        self._append_to_self_log(iteration, time_str, tool_calls, reflection_text, error)

    async def _call_llm_for_reflection(self, prompt: str) -> str:
        """Make a minimal LLM call for self-reflection (no tools, no history)."""
        messages = [
            {"role": "system", "content": "你是一个专注自我反思的 AI Agent。请简洁直接地回答，不要废话。"},
            {"role": "user", "content": prompt},
        ]

        # Use the public LLM interface the same way nanobot does
        # Import here to avoid circular deps — runner handles the provider
        from nanobot.agent.runner import AgentRunner

        # We need a provider to call.  The cleanest way without access to the
        # running session's provider is to create a minimal one from config.
        from nanobot.config.loader import load_config
        from nanobot.providers.factory import make_provider

        config = load_config()
        provider = make_provider(config)
        response = await provider.chat(
            messages=messages,
            tools=None,  # reflection is text-only, no tools
            max_tokens=512,
            temperature=0.3,
        )
        return response.content or ""

    def _append_to_self_log(
        self,
        iteration: int | str,
        time_str: str,
        tool_calls: list[dict],
        reflection_text: str,
        error: str | None,
    ) -> None:
        """Append one self-reflection block to self_log.md."""
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Detect discomfort signals
        has_error = bool(error)
        has_discomfort = "别扭" in reflection_text or "不对" in reflection_text or "错误" in reflection_text

        header = (
            f"## Iteration {iteration} — {time_str}\n"
            f"> Status: {'⚠️ discomfort' if has_discomfort else '✅'} | "
            f"{'❌ error' if has_error else '✅ no error'} | "
            f"tools: {len(tool_calls)}\n"
        )
        body = f"\n{reflection_text}\n\n"

        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(body)

    def _resolve_workspace(self, context: AgentHookContext) -> Path | None:
        if context.workspace:
            return context.workspace
        p = Path.cwd()
        for _ in range(6):
            if (p / "SOUL.md").exists():
                return p
            parent = p.parent
            if parent == p:
                break
            p = parent
        return Path.home() / ".nanobot" / "workspace"