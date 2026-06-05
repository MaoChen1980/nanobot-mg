"""
SelfReflectHook: metrics accumulator + LLM suspect extraction.

Phase 2 of the self-evolution feedback loop. Accumulates metrics across turns,
then calls LLM (no tools, no context) to flag **suspects** in:
- Hook source code (self_bug)
- LLM behavior patterns (behavior / correction / knowledge / decision)

Output: structured suspects for SelfInsightHook to inject into the agent loop.
The agent loop has the context and tools to judge whether each suspect is real.
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
from nanobot.providers.base import LLMProvider


# --- Reflection prompt -------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """\
你是 nanobot，正在回顾 this session 的对话。

你没有上下文、没有工具、不在 agent loop。你的工作是**挑出所有可疑的东西**——
你的输出都是 suspect，留给主循环验证。宁可误报，不可漏报。普通、正常、没有明确问题的行为也要质疑。

## 必须要怀疑的方向

以下是必须检查的怀疑方向。不要跳过任何一个。

| 类型 | 怀疑方向 | 说人话 |
|------|----------|--------|
| **behavior** | 这模式看起来效率不高、不够自然 | "总在同一个文件上改"、"频繁调用同一个工具"、"改了又改" |
| **knowledge** | 这个"知识"只是出于偶然，不是稳定事实 | "当时这么理解的，不一定对" |
| **decision** | 看起来像刻意的决定，也可能只是随手 | "选了 A 没选 B，不一定有理由" |
| **correction** | 用户纠正过什么 | "用户上次说不要这样做" |
| **self_bug** | 代码哪里看着不对 | "这里多了一次计数？" |

**注意：** `behavior` 是最容易找到信号的类型。工具调用频次、重复度、时序模式都在 metrics 里——先从这里入手。

## 输出格式

```json
{
  "findings": [
    {
      "type": "behavior|knowledge|decision|correction|self_bug",
      "content": "哪个具体指标或代码、为什么觉得可疑",
      "relevance": "这条怀疑如果成立，会在什么场景下被用到"
    }
  ]
}
```

**你必须在任何时候都输出至少 1 条 finding。** 可以在你看起来最没有问题的度量里也要去质疑它。没有零结果。
"""

REFLECTION_USER_TEMPLATE = """\
## Execution Log

{metrics_summary}

## Hook Source Code for Self-Review

{hook_code}

## 可疑点

你只有以上信息——metrics + hook 代码。逐一检查：

1. **代码** (→ type: `self_bug`): 哪里看着不对就标出来。多一次计数、
   少一个判断、异常没处理、变量名写错……

2. **模式** (→ type: `behavior` / `correction` / `knowledge` / `decision`):
   从 execution log 看，有没有什么重复模式看起来不太对劲？
   比如总在调用同一个工具、读同一个文件、走同样的弯路。

你不在 agent loop，无法验证——没关系，全部当成 suspect 输出就行。
"""

# --- Hook files for self-review ------------------------------------------------

_HOOKS_DIR = Path(__file__).resolve().parent
_HOOK_FILES = ["self_review.py", "self_reflect.py", "self_insight_hook.py"]


RESOLVED_FILE = Path.home() / ".nanobot" / "self_improve" / "resolved_findings.jsonl"


def _read_resolved_ids(max_ids: int = 200) -> set[str]:
    """Read resolved finding IDs from JSONL file (one ID per line)."""
    if not RESOLVED_FILE.exists():
        return set()
    ids: set[str] = set()
    try:
        lines = RESOLVED_FILE.read_text(encoding="utf-8").strip().splitlines()
        # Keep only the last max_ids to prevent unbounded growth
        for line in lines[-max_ids:]:
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

    LOG_FILE = Path.home() / ".nanobot" / "self_improve" / "self_log.md"
    FINDINGS_FILE = Path.home() / ".nanobot" / "self_improve" / "self_reflect_findings.json"
    DEFAULT_INTERVAL = 15  # fire once every N turns

    def __init__(self, reraise: bool = False, interval: int | None = None) -> None:
        super().__init__(reraise)
        self._turn_count = 0  # user turns (for interval check)
        self._entries_accumulated = []
        self._interval = interval if interval is not None else self.DEFAULT_INTERVAL
        # Optional provider override — if set, _call_llm uses it instead of
        # make_provider(config). The agent loop calls set_provider() after
        # discovery so reflection uses the same model as the main task.
        self._provider: LLMProvider | None = None
        self._model: str | None = None

    def set_provider(self, provider: LLMProvider, model: str | None = None) -> None:
        """Inject the provider/model the main agent is using."""
        self._provider = provider
        if model is not None:
            self._model = model

    # -- after_iteration: accumulate metrics in memory ------------------------

    async def after_iteration(self, context: AgentHookContext) -> None:
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
        real_messages = [
            m for m in (context.messages or [])
            if m.get("_source") != "self_insight_hook"
        ]
        return {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iteration": context.iteration,
            "tool_calls": tool_calls_data,
            "tool_count": len(tool_calls_data),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "message_count": len(real_messages),
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

        # Fire reflection and reset batch
        entries = self._entries_accumulated
        self._entries_accumulated = []
        self._turn_count = 0

        try:
            await self._run_turn_reflection(entries)
        except Exception:
            logger.debug("SelfReflectHook.after_turn failed")

    async def _run_turn_reflection(self, entries: list[dict]) -> None:
        """Build a summary from all iterations, fire LLM, save results."""
        total_tokens = sum(e.get("usage", {}).get("total_tokens", 0) for e in entries)
        total_tool_calls = sum(e.get("tool_count", 0) for e in entries)
        errors = [e for e in entries if e.get("error")]
        iteration_range = f"#{min(e['iteration'] for e in entries)}-#{max(e['iteration'] for e in entries)}"
        time_str = entries[-1]["time"]

        # Tool call frequency (all tools, not just repeated)
        tool_name_counts: dict[str, int] = {}
        file_read_targets: dict[str, int] = {}
        for e in entries:
            for tc in e.get("tool_calls", []):
                tool_name_counts[tc["name"]] = tool_name_counts.get(tc["name"], 0) + 1
                if tc["name"] in ("read_file", "edit_file", "write_file"):
                    path = (
                        tc.get("arguments", {}).get("file_path")
                        or tc.get("arguments", {}).get("path")
                        or ""
                    )
                    if path:
                        key = tc["name"] + ":" + path
                        file_read_targets[key] = file_read_targets.get(key, 0) + 1

        freq_lines = []
        if tool_name_counts:
            freq_lines.append("  Tool frequency:")
            for name, cnt in sorted(tool_name_counts.items(), key=lambda x: -x[1]):
                freq_lines.append(f"    {name}: {cnt}")
        freq_summary = "\n".join(freq_lines)

        file_focus_lines = []
        repeated_file = {k: v for k, v in file_read_targets.items() if v >= 2}
        if repeated_file:
            file_focus_lines.append("  File focus (≥2 accesses):")
            for key, cnt in sorted(repeated_file.items(), key=lambda x: -x[1]):
                file_focus_lines.append(f"    {key} x{cnt}")
        file_summary = "\n".join(file_focus_lines)

        # Per-iteration tool sequence for pattern detection
        seq_lines = ["  Tool call sequence:"]
        for e in entries:
            names = [tc["name"] for tc in e.get("tool_calls", [])]
            if names:
                seq_lines.append(f"    iter#{e['iteration']}: {' → '.join(names)}")
            else:
                seq_lines.append(f"    iter#{e['iteration']}: (no tools)")
        seq_summary = "\n".join(seq_lines)

        metrics_text = (
            f"  Iterations: {iteration_range} @ {time_str}\n"
            f"  Total iterations: {len(entries)}\n"
            f"  Total token usage: {total_tokens}\n"
            f"  Total tool calls: {total_tool_calls}\n"
            f"  Errors: {len(errors)}\n"
            f"{freq_summary}\n"
            f"{file_summary}\n"
            f"{seq_summary}"
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
        """Make a minimal LLM call for structured findings extraction.

        Uses self._provider if set via set_provider(). Otherwise falls
        back to make_provider(config), which yields the config-default provider.
        """
        if self._provider is None:
            from nanobot.config.loader import load_config
            from nanobot.providers.factory import make_provider

            config = load_config()
            provider = make_provider(config)
        else:
            provider = self._provider

        response = await provider.chat_stream(
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": REFLECTION_USER_TEMPLATE.format(
                    metrics_summary=metrics_text,
                    hook_code=hook_code,
                )},
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
        """Write human-readable reflection to markdown log.

        Always writes to log even when findings is empty — this ensures the file
        exists for external readers (e.g., daily-evolution cron) to analyze.
        When findings is empty, writes a 'nothing actionable' status instead.
        """
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
            finding_lines = ["(nothing actionable)"]

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

        # Cap log at 500 lines, keep last 200
        MAX_LOG_LINES = 500
        KEEP_LAST = 200
        try:
            lines = self.LOG_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_LOG_LINES:
                truncated = lines[-KEEP_LAST:]
                self.LOG_FILE.write_text("\n".join(truncated) + "\n", encoding="utf-8")
        except OSError:
            pass
