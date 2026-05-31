"""
SelfReflectHook: LLM-powered self-reflection with hook code self-review.

Phase 2 of the self-evolution feedback loop. Accumulates metrics across turns,
then calls LLM to produce structured findings including:
- Project knowledge, decisions, behavior patterns, corrections
- **self_bug**: defects in hook source code
- LLM behavior pattern analysis (bad guesses, repeated errors)

The hook reads its own source code and includes it in the reflection prompt
so the LLM can find bugs in the hooks themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


# --- Reflection prompt -------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """\
你是 nanobot，正在对 this session 进行自我反思。

你的 task 是：回顾 this session 对话，提取**值得记住的信息**。

## Filtering Criteria

只记录**同时满足以下两个条件**的信息：
1. 这不是 LLM 训练数据中已有的通用知识（而是项目特有的架构决策、配置约定、历史原因等）
2. 这条信息在当前 task 或可预见的未来 task 中会被复用

符合条件的信息类型：

| 类型 | 说明 | 示例 |
|------|------|------|
| **knowledge** | 项目特定的技术知识 | "这个项目的 provider 初始化有依赖顺序" |
| **decision** | 架构/设计决策及其理由 | "选 MiniMax 是因为长上下文" |
| **behavior** | 更高效的执行模式 | "改 provider 前应该先看所有 consumer" |
| **correction** | 用户纠正的认知偏差 | "用户说 cut 应按 turn 数不是按消息数" |
| **self_bug** | nanobot 自身代码中的缺陷 | "SelfReflectHook.after_turn 行 149 多了一次 _turn_count += 1" |

不符合以上条件的信息：**不要记录**。即使它看起来很重要。

## Output Format

```json
{
  "findings": [
    {
      "type": "knowledge|decision|behavior|correction|self_bug",
      "content": "具体信息",
      "relevance": "这条会在什么 task 场景下被复用"
    }
  ]
}
```

如果没有任何符合条件的信息，输出 {"findings": []}
"""

REFLECTION_USER_TEMPLATE = """\
## Execution Log

{metrics_summary}

## Hook Source Code for Self-Review

{hook_code}

Analyze the execution log AND the hook source code above. Check for:

1. **Hook code bugs** (→ type: `self_bug`): double counting, off-by-one, fragile regex parsing,
   incorrect state management, resource leaks, logic errors in detection/injection flow.

2. **LLM behavior patterns** (→ type: `correction` or `behavior`): Is the LLM making
   repeated bad guesses (wrong file paths, calling tools without preparation)?
   Are there recurring error patterns from incorrect tool usage?

请输出 JSON findings。
"""

# --- Hook files for self-review ------------------------------------------------

_HOOKS_DIR = Path(__file__).resolve().parent
_HOOK_FILES = ["self_review.py", "self_reflect.py", "self_insight_hook.py"]


RESOLVED_FILE = Path.home() / ".nanobot" / "agent" / "resolved_findings.jsonl"


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


def _read_hook_sources() -> str:
    """Read hook .py files and format as markdown code blocks."""
    parts: list[str] = []
    for name in _HOOK_FILES:
        path = _HOOKS_DIR / name
        if path.exists():
            code = path.read_text(encoding="utf-8")
            parts.append(f"### {path.resolve().as_posix()}\n\n```python\n{code}\n```")
    return "\n\n".join(parts)


SELF_AWARENESS_PROMPT = """
## Self-Awareness

在整个对话过程中保持自我觉察。在每次工具调用之前，快速审视：
- 我正在调用哪个工具？这是第几次？
- 我的行为模式看起来像什么？（进步、卡住、转圈？）
- 如果从外部观察，我会怎么评价自己当前的行动？

特别警惕以下模式：
- **重复编辑同一个文件**：如果你连续两次修改同一文件，停下来思考之前的修改为什么没解决问题
- **重复执行同一工具**：如果同一个工具被反复调用且结果相似，可能是方法不对，换一种方式
- **忽视工具错误信息**：工具返回的错误不是让你重试，而是告诉你当前的思路有问题
"""


# --- Hook ---------------------------------------------------------------------

class SelfReflectHook(AgentHook):
    """Accumulate per-iteration metrics and fire one LLM reflection every N turns.

    Instead of one reflection per turn, batch across multiple turns to:
    - Reduce LLM cost (1 call per N turns vs 1 call per turn)
    - Accumulate more data for better pattern detection
    """

    LOG_FILE = Path.home() / ".nanobot" / "agent" / "self_log.md"
    FINDINGS_FILE = Path.home() / ".nanobot" / "agent" / "self_reflect_findings.json"
    DEFAULT_INTERVAL = 15  # fire once every N turns

    def __init__(self, reraise: bool = False, interval: int | None = None) -> None:
        super().__init__(reraise)
        self._iteration = 0  # LLM iterations (for entry labeling)
        self._turn_count = 0  # user turns (for interval check)
        self._entries_accumulated = []
        self._interval = interval if interval is not None else self.DEFAULT_INTERVAL

    # -- after_iteration: accumulate metrics in memory ------------------------

    async def after_iteration(self, context: AgentHookContext) -> None:
        self._iteration += 1
        try:
            self._entries_accumulated.append(self._build_entry(context))
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
            "iteration": self._iteration,
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

    # -- before_iteration: no-op (reflection fires in after_turn) -------------

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    # -- after_turn: batch reflection every N turns --------------------------------

    async def after_turn(self) -> None:
        if not self._entries_accumulated:
            return

        self._turn_count += 1
        if self._turn_count < self._interval:
            return  # keep accumulating

        # Fire reflection and reset
        entries = self._entries_accumulated
        self._entries_accumulated = []
        self._turn_count = 0
        self._iteration = 0

        try:
            await self._run_turn_reflection(entries)
        except Exception:
            logger.debug("SelfReflectHook.after_turn failed")

    async def _run_turn_reflection(self, entries: list[dict]) -> None:
        """Build a summary from all iterations, fire LLM, save results."""
        total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in entries)
        total_tool_calls = sum(e.get("tool_count", 0) for e in entries)
        errors = [e for e in entries if e.get("error")]
        iteration_range = f"#{entries[0]['iteration']}-#{entries[-1]['iteration']}"
        time_str = entries[-1]["time"]

        # Detect repeated tool patterns
        tool_name_counts: dict[str, int] = {}
        for e in entries:
            for tc in e.get("tool_calls", []):
                tool_name_counts[tc["name"]] = tool_name_counts.get(tc["name"], 0) + 1
        repeated = {name: cnt for name, cnt in tool_name_counts.items() if cnt >= 3}
        rep_summary = ""
        if repeated:
            parts = [f"    {name} x {cnt}" for name, cnt in sorted(repeated.items())]
            rep_summary = "  Repeated tools:\n" + "\n".join(parts) + "\n"

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
            parts = [f"    {p} x {cnt} edits" for p, cnt in sorted(repeated_edits.items())]
            edit_summary = "  Repeated edits:\n" + "\n".join(parts) + "\n"

        metrics_text = (
            f"  Iterations: {iteration_range} @ {time_str}\n"
            f"  Total iterations: {len(entries)}\n"
            f"  Total token usage: {total_tokens}\n"
            f"  Total tool calls: {total_tool_calls}\n"
            f"  Errors: {len(errors)}\n"
            f"{rep_summary}"
            f"{edit_summary}"
        )

        # Read hook source code for self-review
        hook_code = _read_hook_sources()

        # Call LLM for structured findings
        findings = await self._call_for_findings(metrics_text, hook_code)

        # Save findings JSON (consumed by SelfInsightHook)
        self._save_findings(findings, iteration_range, time_str)

        # Also write readable log for human review
        self._append_to_log(iteration_range, time_str, total_tool_calls, errors, findings)

    async def _call_for_findings(self, metrics_text: str, hook_code: str) -> list[dict[str, Any]]:
        """Call LLM to extract task-relevant findings from this turn."""
        try:
            response = await self._call_llm(metrics_text, hook_code)
        except Exception as exc:
            logger.debug("SelfReflectHook: LLM call failed: {}", exc)
            return []
        return self._parse_findings(response)

    async def _call_llm(self, metrics_text: str, hook_code: str) -> str:
        """Make a minimal LLM call for structured findings extraction."""
        from nanobot.config.loader import load_config
        from nanobot.providers.factory import make_provider

        config = load_config()
        provider = make_provider(config)
        response = await provider.chat(
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": REFLECTION_USER_TEMPLATE.format(metrics_summary=metrics_text, hook_code=hook_code)},
            ],
            tools=None,
            max_tokens=1024,
            temperature=0.3,
        )
        return response.content or ""

    @staticmethod
    def _parse_findings(raw: str) -> list[dict[str, Any]]:
        """Parse JSON findings from LLM response."""
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return []

        findings = result.get("findings", []) if isinstance(result, dict) else []
        if not isinstance(findings, list):
            return []

        valid = []
        for f in findings:
            if isinstance(f, dict) and f.get("type") and f.get("content"):
                ftype = f["type"]
                if ftype in ("knowledge", "decision", "behavior", "correction", "self_bug"):
                    valid.append(f)
        return valid

    @staticmethod
    def _finding_id(content: str) -> str:
        """Generate a stable finding ID from content hash."""
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _save_findings(
        self, findings: list[dict[str, Any]], iteration_range: str, time_str: str
    ) -> None:
        """Write structured findings to JSON file for SelfInsightHook."""
        self.FINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        for f in findings:
            if "id" not in f and f.get("content"):
                f["id"] = self._finding_id(f["content"])

        # Read existing resolved IDs to carry forward
        resolved_ids = _read_resolved_ids()

        payload = {
            "saved_at": time_str,
            "iteration_range": iteration_range,
            "source": "self_reflect",
            "findings": findings,
            "resolved_ids": list(resolved_ids),
        }
        self.FINDINGS_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_to_log(
        self,
        iteration_range: str,
        time_str: str,
        total_tool_calls: int,
        errors: list[dict],
        findings: list[dict[str, Any]],
    ) -> None:
        """Write human-readable reflection to markdown log."""
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        finding_lines: list[str] = []
        if findings:
            for f in findings:
                ftype = f["type"]
                content = f["content"]
                relevance = f.get("relevance", "")
                line = f"- **{ftype}**: {content}"
                if relevance:
                    line += f"  \n  -> {relevance}"
                finding_lines.append(line)
        else:
            finding_lines.append("(nothing actionable)")

        has_error = bool(errors)
        findings_text = "\n".join(finding_lines)
        header = (
            f"## Turn {iteration_range} -- {time_str}\n"
            f"> Status: {len(findings)} finding(s) | "
            f"{'error' if has_error else 'ok'} | "
            f"tools: {total_tool_calls}\n"
        )
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(f"\n{findings_text}\n\n")
