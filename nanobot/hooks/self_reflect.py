"""
SelfReflectHook: LLM-powered self-reflection after each full user turn.

Phase 1 (SelfReviewHook) captured raw metrics.
Phase 2 (this hook) adds LLM self-reflection.
Phase 3 (SelfInsightHook) injects insights back into context.

Together they form the self-evolution feedback loop:
  SelfReviewHook (capture) → SelfReflectHook (reflect) → SelfInsightHook (inject)

Strategy: per-turn accumulation — collects iteration metrics in memory during
``after_iteration``, then fires ONE LLM reflection call in ``after_turn``.

This avoids:
  - Per-iteration file I/O and pending-file management
  - Nested LLM calls inside the agent loop (after_turn fires after the turn ends)
  - Duplicate reflections on retry/re-entry
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


# ─── Reflection questions ────────────────────────────────────────────────────
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

SELF_AWARENESS_PROMPT = """
## 自我感知

在整个对话过程中保持自我觉察。在每次工具调用之前，快速审视：
- 我正在调用哪个工具？这是第几次？
- 我的行为模式看起来像什么？（进步、卡住、转圈？）
- 如果从外部观察，我会怎么评价自己当前的行动？

特别警惕以下模式：
- **重复编辑同一个文件**：如果你连续两次修改同一文件，停下来思考之前的修改为什么没解决问题
- **重复执行同一工具**：如果同一个工具被反复调用且结果相似，可能是方法不对，换一种方式
- **忽视工具错误信息**：工具返回的错误不是让你重试，而是告诉你当前的思路有问题
"""


# ─── Main hook ────────────────────────────────────────────────────────────────
class SelfReflectHook(AgentHook):
    """Accumulate per-iteration metrics and fire one LLM reflection per turn."""

    LOG_FILE = Path.home() / ".nanobot" / "agent" / "self_log.md"

    def __init__(self, reraise: bool = False) -> None:
        super().__init__(reraise)
        self._iteration_entries: list[dict] = []
        self._iteration_count = 0

    # ── after_iteration: accumulate metrics in memory ───────────────────────

    async def after_iteration(self, context: AgentHookContext) -> None:
        self._iteration_count += 1
        try:
            self._iteration_entries.append(self._build_entry(context))
        except Exception:
            logger.debug("SelfReflectHook.after_iteration failed silently")

    def _build_entry(self, context: AgentHookContext) -> dict:
        tool_calls_data = [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in (context.tool_calls or [])
        ]
        usage = context.usage or {}
        return {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iteration": self._iteration_count,
            "tool_calls": tool_calls_data,
            "tool_count": len(tool_calls_data),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "message_count": len(context.messages),
            "final_content_len": len(context.final_content or ""),
            "error": context.error,
        }

    # ── before_iteration: no-op (reflection fires in after_turn) ────────────

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    # ── after_turn: fire one reflection for the entire turn ─────────────────

    async def after_turn(self) -> None:
        if not self._iteration_entries:
            return

        entries = self._iteration_entries
        self._iteration_entries = []  # reset for next turn

        try:
            await self._run_turn_reflection(entries)
        except Exception:
            logger.debug("SelfReflectHook.after_turn failed")

    async def _run_turn_reflection(self, entries: list[dict]) -> None:
        """Build a summary from all iterations and fire one LLM reflection."""
        total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in entries)
        total_tool_calls = sum(e.get("tool_count", 0) for e in entries)
        errors = [e for e in entries if e.get("error")]
        iteration_range = f"#{entries[0]['iteration']}–#{entries[-1]['iteration']}"
        time_str = entries[-1]["time"]

        # Detect repeated tool patterns
        tool_name_counts: dict[str, int] = {}
        for e in entries:
            for tc in e.get("tool_calls", []):
                tool_name_counts[tc["name"]] = tool_name_counts.get(tc["name"], 0) + 1
        repeated = {name: cnt for name, cnt in tool_name_counts.items() if cnt >= 3}
        rep_summary = ""
        if repeated:
            parts = [f"    {name} × {cnt}" for name, cnt in sorted(repeated.items())]
            rep_summary = "  重复工具调用:\n" + "\n".join(parts) + "\n"

        # Detect same-file edits
        file_edit_targets: dict[str, int] = {}
        for e in entries:
            for tc in e.get("tool_calls", []):
                if tc["name"] == "edit_file":
                    path = (
                        tc.get("arguments", {}).get("file_path")
                        or tc.get("arguments", {}).get("path")
                        or ""
                    )
                    if path:
                        file_edit_targets[path] = file_edit_targets.get(path, 0) + 1
        edit_summary = ""
        repeated_edits = {p: c for p, c in file_edit_targets.items() if c >= 3}
        if repeated_edits:
            parts = [f"    {p} × {cnt}次编辑" for p, cnt in sorted(repeated_edits.items())]
            edit_summary = "  重复编辑:\n" + "\n".join(parts) + "\n"

        metrics_text = (
            f"  Turns: {iteration_range} @ {time_str}\n"
            f"  Total iterations: {len(entries)}\n"
            f"  Total token usage: {total_tokens}\n"
            f"  Total tool calls: {total_tool_calls}\n"
            f"  Errors: {len(errors)}\n"
            f"{rep_summary}"
            f"{edit_summary}"
        )

        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            metrics_summary=metrics_text,
            q0=REFLECTION_QUESTIONS[0],
            q1=REFLECTION_QUESTIONS[1],
            q2=REFLECTION_QUESTIONS[2],
            q3=REFLECTION_QUESTIONS[3],
        )

        try:
            reflection_text = await self._call_llm_for_reflection(prompt)
        except Exception as exc:
            logger.debug("SelfReflectHook: LLM call failed: {}", exc)
            reflection_text = f"[reflection unavailable: {exc}]"

        combined = SELF_AWARENESS_PROMPT + "\n\nTurn reflection:\n\n" + reflection_text
        self._append_to_log(iteration_range, time_str, total_tool_calls, errors, combined)

    async def _call_llm_for_reflection(self, prompt: str) -> str:
        """Make a minimal LLM call for self-reflection (no tools, no history)."""
        messages = [
            {"role": "system", "content": "你是一个专注自我反思的 AI Agent。请简洁直接回答每个问题，每个答案不超过50字，禁止任何开场白（如\"好的\"、\"我来回答\"）。"},
            {"role": "user", "content": prompt},
        ]

        from nanobot.config.loader import load_config
        from nanobot.providers.factory import make_provider

        config = load_config()
        provider = make_provider(config)
        response = await provider.chat(
            messages=messages,
            tools=None,
            max_tokens=512,
            temperature=0.3,
        )
        return response.content or ""

    def _append_to_log(
        self,
        iteration_range: str,
        time_str: str,
        total_tool_calls: int,
        errors: list[dict],
        reflection_text: str,
    ) -> None:
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        has_error = bool(errors)
        has_discomfort = any(
            kw in reflection_text for kw in ("别扭", "不对", "错误", "重复")
        )
        header = (
            f"## Turn {iteration_range} — {time_str}\n"
            f"> Status: {'⚠️ discomfort' if has_discomfort else '✅'} | "
            f"{'❌ error' if has_error else '✅ no error'} | "
            f"tools: {total_tool_calls}\n"
        )
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(f"\n{reflection_text}\n\n")

