"""
SelfDetectHook: metrics accumulator + LLM suspect extraction.

Phase 2 of the self-evolution feedback loop. Accumulates metrics across turns,
then calls LLM (no tools, no context) to flag **suspects** in:
- Hook source code (self_bug)
- LLM behavior patterns (behavior / correction / knowledge / decision)

Output: structured suspects for SelfFixHook to inject into the agent loop.
The agent loop has the context and tools to judge whether each suspect is real.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from nanobot.agent.llm_context import chat_stream_with_retry


# --- Reflection prompt -------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """\
## 任务
审查 session 执行日志和 hook 代码结构，挑出所有可疑的模式和代码问题。

输出作为 suspect 提交给主循环验证——宁可误报，不可漏报。普通、正常、没有明确问题的行为也要质疑。

## 输出要求
按以下 JSON 格式输出 findings：

```
{
  "findings": [
    {
      "type": "behavior|knowledge|decision|correction|self_bug",
      "content": "具体指标或代码片段、为什么觉得可疑",
      "relevance": "这条怀疑如果成立，会在什么场景下被用到"
    }
  ]
}
```

## 怀疑方向（必须检查所有类型，不要跳过）

- **behavior**: 这模式看起来效率不高、不够自然——"总在同一个文件上改"、"频繁调用同一个工具"、"改了又改"
- **knowledge**: 这个"知识"只是出于偶然，不是稳定事实——"当时这么理解的，不一定对"
- **decision**: 看起来像刻意的决定，也可能只是随手——"选了 A 没选 B，不一定有理由"
- **correction**: 用户纠正过什么——"用户上次说不要这样做"
- **self_bug**: 代码哪里看着不对——"这里多了一次计数？"

**注意：** `behavior` 是最容易找到信号的类型。工具调用频次、重复度、时序模式都在 metrics 里——先从这里入手。

## 约束

- 输出 JSON 格式，不要多余文字
- 宁误报不漏报；一个 finding 都没有时输出空列表 `{"findings": []}`
- 不要为了满足"必须有 finding"而强制制造怀疑
"""

REFLECTION_USER_TEMPLATE = """\
## 输入数据

{metrics_summary}

## Hook 模块结构

{hook_code}

## 分析步骤

1. 检查行为模式（type: behavior/correction/knowledge/decision）：从 execution log 中找重复模式——频繁调用同一个工具、读同一个文件、走同样的弯路
2. 检查代码结构（type: self_bug）：从 hook 的类和方法签名看结构问题——职责过多、命名歧义、缺少预期的入口点。注意：看不到完整源码，只能看结构
"""

# --- Hook files for self-review ------------------------------------------------

_HOOKS_DIR = Path(__file__).resolve().parent
_HOOK_FILES = ["self_log_hook.py", "self_detect_hook.py"]


RESOLVED_FILE = Path.home() / ".nanobot" / "self_improve" / "resolved_findings.jsonl"


def _fmt_args(args: dict) -> str:
    """Compact argument preview for reflection LLM.

    Shows file_path and key parameters; truncates long values.
    """
    MAX_VAL = 60
    MAX_ARGS = 4
    parts = []
    for k, v in args.items():
        if len(parts) >= MAX_ARGS:
            parts.append("...")
            break
        if isinstance(v, str):
            if len(v) > MAX_VAL:
                v = v[:MAX_VAL] + "..."
            parts.append(f"{k}={v}")
        elif isinstance(v, (int, float, bool)):
            parts.append(f"{k}={v}")
        elif v is None:
            parts.append(f"{k}=null")
        else:
            s = str(v)
            if len(s) > MAX_VAL:
                s = s[:MAX_VAL] + "..."
            parts.append(f"{k}={s}")
    return ", ".join(parts)


# --- User negative signal patterns ---------------------------------------------
# Extended patterns: Chinese + English, ordered by specificity (more specific first).

_USER_NEGATIVE_PATTERNS: dict[str, tuple[str, ...]] = {
    "rejection": (
        "不要", "别", "不对", "不是", "不是这个", "不行",
        "no", "nope", "wrong", "incorrect", "not what I",
    ),
    "correction": (
        "错了", "我说的是", "我问的是", "你理解错了", "不是这个意思",
        "you misunderstood", "that's not", "actually", "correction",
        "let me clarify", "what I meant", "重新来", "不是这样的",
    ),
    "redo": (
        "重新", "重来", "再来", "换一个", "重做",
        "redo", "try again", "start over", "from scratch",
    ),
    "interruption": (
        "停", "stop", "够了", "别说了", "别做了", "停下",
        "enough", "cancel", "abort", "hold on",
    ),
    "confusion": (
        "不明白", "没看懂", "什么意思", "你确定",
        "confused", "not clear", "doesn't make sense", "huh",
    ),
}

# Feedback file written by _write_user_corrections() — consumed by MemoryExtractor.
_FEEDBACK_FILE = Path.home() / ".nanobot" / "self_improve" / "user_corrections.jsonl"
_MAX_FEEDBACK_CONTEXT = 200  # max chars of message context preserved per signal


def _detect_user_signals(messages: list[dict]) -> list[dict]:
    """Scan real user messages for negative feedback signals.

    Returns structured list with type, matched keyword, context excerpt, and timestamp.
    """
    signals: list[dict] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        for signal_type, keywords in _USER_NEGATIVE_PATTERNS.items():
            for kw in keywords:
                if kw in content:
                    idx = content.index(kw)
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(kw) + 40)
                    context = content[start:end].strip()
                    if len(context) > _MAX_FEEDBACK_CONTEXT:
                        context = context[:_MAX_FEEDBACK_CONTEXT] + "..."
                    signals.append({
                        "type": signal_type,
                        "matched": kw,
                        "context": context,
                        "time": now,
                    })
                    break  # one match per message per signal type
    return signals


def _aggregate_signals(signal_records: list[dict]) -> dict[str, int]:
    """Aggregate structured signal records back to per-type counts."""
    counts: dict[str, int] = {}
    for s in signal_records:
        t = s["type"]
        counts[t] = counts.get(t, 0) + 1
    return counts


def _write_user_corrections(signal_records: list[dict]) -> None:
    """Persist correction/rejection signals to a structured JSONL file.

    Written to ``~/.nanobot/self_improve/user_corrections.jsonl``.
    MemoryExtractor reads this file to learn from user corrections.

    Only strong-correction signals are persisted (correction + rejection),
    to avoid noise from casual redo/confusion patterns.
    """
    strong = [s for s in signal_records if s["type"] in ("correction", "rejection")]
    if not strong:
        return
    try:
        _FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = "\n".join(json.dumps(s, ensure_ascii=False) for s in strong)
        with open(_FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(lines + "\n")
    except OSError:
        logger.debug("SelfDetectHook: failed to write user_corrections.jsonl")


def _read_resolved_ids() -> set[str]:
    """Read resolved finding IDs from JSONL file (one ID per line).

    Reads ALL lines — no truncation. resolved_findings.jsonl grows slowly
    (one ID per resolved finding) and bounded set prevents unbounded memory.
    Truncation to 200 was causing old resolved IDs to be forgotten, making
    resolved findings re-appear as new.
    """
    if not RESOLVED_FILE.exists():
        return set()
    ids: set[str] = set()
    try:
        lines = RESOLVED_FILE.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
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


def _summarize_hook_sources() -> str:
    """Structural summary of hook modules (file + class + method names, no body)."""
    parts: list[str] = []
    for name in _HOOK_FILES:
        path = _HOOKS_DIR / name
        if not path.exists():
            continue
        code = path.read_text(encoding="utf-8")
        lines = code.splitlines()
        items: list[tuple[str, list[str]]] = []  # [(class_name, [methods])]
        current_class: str | None = None
        current_methods: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("class ") and stripped.endswith(":"):
                if current_class:
                    items.append((current_class, current_methods))
                current_class = stripped.split("class ")[1].rstrip(":").split("(")[0]
                current_methods = []
            elif stripped.startswith(("async def ", "def ")) and stripped.endswith(":"):
                indent = len(line) - len(line.lstrip())
                if indent >= 4 and current_class:
                    method_name = stripped.split("def ")[1].rstrip(":")
                    current_methods.append(method_name)
        if current_class:
            items.append((current_class, current_methods))
        summary_lines = [f"### {name}"]
        for cls, methods in items:
            summary_lines.append(
                f"  class {cls}: {', '.join(methods)}" if methods else f"  class {cls}"
            )
        parts.append("\n".join(summary_lines))
    return "\n\n".join(parts)


# --- Hook ---------------------------------------------------------------------

class SelfDetectHook(AgentHook):
    """Accumulate per-iteration metrics and fire one LLM reflection every N turns.

    Instead of one reflection per turn, batch across multiple turns to:
    - Reduce LLM cost (1 call per N turns vs 1 call per turn)
    - Accumulate more data for better pattern detection
    """

    LOG_FILE = Path.home() / ".nanobot" / "self_improve" / "self_log.md"
    FINDINGS_FILE = Path.home() / ".nanobot" / "self_improve" / "self_reflect_findings.json"
    RESOLVED_FILE = Path.home() / ".nanobot" / "self_improve" / "resolved_findings.jsonl"
    DEFAULT_INTERVAL = 3  # fire once every N turns
    DEFAULT_ITER_INTERVAL = 20  # also fire every N iterations (catches short sessions)

    def __init__(self, reraise: bool = False, interval: int | None = None) -> None:
        super().__init__(reraise)
        self._turn_count = 0  # user turns (for interval check)
        self._entries_accumulated = []
        self._interval = interval if interval is not None else self.DEFAULT_INTERVAL
        self._total_iterations = 0  # total iterations across turns
        self._workspace: Path | None = None
        self._project_type: str = "unknown"  # detected once in set_workspace()

    def set_workspace(self, workspace_path: Path) -> None:
        """Set the workspace path for writing findings doc.

        Called by AgentLoop._discover_hooks() after discovery, following the
        same pattern as set_provider().
        """
        self._workspace = workspace_path
        # Detect project_type once here instead of redundantly in _detect_project_type
        self._project_type = self._detect_project_type()

    # -- after_iteration: accumulate metrics in memory ------------------------

    async def after_iteration(self, context: AgentHookContext) -> None:
        try:
            self._entries_accumulated.append(self._build_entry(context))
            self._total_iterations += 1
        except Exception:
            logger.debug("SelfDetectHook.after_iteration failed silently")

    async def after_run(self, context: AgentRunHookContext) -> None:
        """Write session summary to shared session context dir.

        Writes findings and metrics to workspace/.self_improve/session_context/{project}/last_session.md.
        ContextInjectHook sets the module-level _session_context_dir in set_workspace
        before this runs, so we use that path directly.
        """
        try:
            # Use shared session context dir from ContextInjectHook
            # If not set, derive from self._workspace
            from nanobot.hooks.context_inject_hook import get_session_context_dir

            scd = get_session_context_dir()
            # project_type already detected and cached in set_workspace()
            project_type = self._project_type
            if scd is None and self._workspace is not None:
                # Fallback: build session context dir ourselves
                scd = (
                    self._workspace
                    / ".self_improve"
                    / "session_context"
                    / project_type
                )
            elif scd is None:
                return

            project_name_map = {
                "android": "mobile-ai-agent",
                "python": "nanobot-mg",
                "trading": "trading",
            }.get(project_type, "unknown")

            scd.mkdir(parents=True, exist_ok=True)
            last_path = scd / "last_session.md"

            # Count rejection/correction signals from accumulated entries
            rejection_count = 0
            correction_count = 0
            tool_counts: dict[str, int] = {}
            for entry in self._entries_accumulated:
                for msg in entry.get("messages", []):
                    role = msg.get("role", "")
                    # Rough proxy: user msgs after tool results often = rejection signals
                    if role == "user" and msg.get("tool_result"):
                        rejection_count += 1
                    if role == "user" and msg.get("content") and any(
                       kw in str(msg.get("content", "")) for kw in ["correction", "actually", "别", "不对"]
                    ):
                        correction_count += 1
                # Count tool calls per name
                for tc in entry.get("tool_calls", []):
                    name = tc.get("name", "unknown")
                    tool_counts[name] = tool_counts.get(name, 0) + 1

            # Build summary
            lines = [
                f"# Session Summary — {project_name_map}",
                "",
                f"**Project type**: {project_type}",
                f"**Total iterations**: {self._total_iterations}",
                f"**Total turns**: {self._turn_count}",
                f"**Rejection signals**: {rejection_count}",
                f"**Correction signals**: {correction_count}",
                "",
                "## Tool usage counts",
                "",
            ]
            for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"- {name}: {count}")

            if self._entries_accumulated:
                lines.append("")
                lines.append("## Last few tool calls")
                last_entries = self._entries_accumulated[-3:]
                for entry in last_entries:
                    for tc in entry.get("tool_calls", []):
                        lines.append(f"- {tc.get('name', '?')}: {str(tc.get('arguments', ''))[:80]}")

            last_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("SelfDetectHook.after_run failed silently")

    def _detect_project_type(self) -> str:
        """Detect project type from self._workspace (mirrors ContextInjectHook logic)."""
        if self._workspace is None:
            return "unknown"
        if (self._workspace / "app" / "build.gradle.kts").exists():
            return "android"
        if (self._workspace / "nanobot" / "hooks").exists():
            return "python"
        if (self._workspace / "workspace").exists() or (self._workspace.parent / "workspace").exists():
            return "trading"
        return "unknown"

    def _build_entry(self, context: AgentHookContext) -> dict:
        tool_calls_data = [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in (context.tool_calls or [])
        ]
        usage = context.usage or {}
        real_messages = [
            m for m in (context.messages or [])
            if m.get("_source") != "self_fix_hook"
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
            "user_signals": _detect_user_signals(real_messages),
        }

    # -- before_iteration: no-op (reflection fires in after_turn) -------------

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    # -- after_turn: batch reflection every N turns --------------------------------

    def _should_fire(self) -> bool:
        """Return True when accumulated entries warrant a reflection pass."""
        if not self._entries_accumulated:
            return False
        return (self._turn_count >= self._interval or
                self._total_iterations >= self.DEFAULT_ITER_INTERVAL)

    async def after_turn(self) -> None:
        if not self._entries_accumulated:
            return

        self._turn_count += 1
        if not self._should_fire():
            return  # keep accumulating

        # Fire reflection and reset batch
        entries = self._entries_accumulated
        self._entries_accumulated = []
        self._turn_count = 0
        self._total_iterations = 0

        try:
            await self._run_turn_reflection(entries)
        except Exception:
            logger.debug("SelfDetectHook.after_turn failed")

    def _write_findings_doc(self, findings: list[dict]) -> None:
        """Write unresolved findings to workspace/framework/self_findings.md.

        Only findings whose IDs are NOT in resolved_findings.jsonl are included.
        If all findings are resolved (or no findings), the doc is removed so
        ContextBuilder has nothing to inject.
        """
        if not self._workspace:
            return

        # Read resolved IDs from the class-level RESOLVED_FILE (overridable in tests)
        resolved_ids: set[str] = set()
        try:
            if self.RESOLVED_FILE.exists():
                resolved_ids = set(
                    line.strip()
                    for line in self.RESOLVED_FILE.read_text(encoding="utf-8").strip().splitlines()
                    if line.strip()
                )
        except OSError:
            pass
        unresolved = [f for f in findings if f.get("id") and f["id"] not in resolved_ids]

        doc_path = self._workspace / "framework" / "self_findings.md"

        if not unresolved:
            if doc_path.exists():
                try:
                    doc_path.unlink()
                except OSError:
                    pass
            return

        parts = [
            "## Self-Evolution Findings",
            "",
            "The following items were flagged by the self-review system. "
            "Inspect each and mark resolved when addressed.",
            "",
            "To mark a finding as resolved, run:",
            "```",
            "echo <finding_id> >> ~/.nanobot/self_improve/resolved_findings.jsonl",
            "```",
            "",
        ]
        for f in unresolved:
            fid = f["id"]
            ftype = f.get("type", "?")
            content = f.get("content", "")
            relevance = f.get("relevance", "")

            parts.append(f"### {fid} ({ftype})")
            parts.append(f"**Content**: {content}")
            if relevance:
                parts.append(f"**Relevance**: {relevance}")
            parts.append(
                f"**Resolve**: `echo {fid} >> ~/.nanobot/self_improve/resolved_findings.jsonl`"
            )
            parts.append("")

        try:
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text("\n".join(parts), encoding="utf-8")
        except OSError:
            logger.debug("SelfDetectHook: failed to write findings doc")

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

        # Per-iteration tool sequence with key arguments
        seq_lines = ["  Tool call sequence:"]
        for e in entries:
            calls = e.get("tool_calls", [])
            if calls:
                parts = []
                for tc in calls:
                    args = tc.get("arguments") or {}
                    # Compact arg summary: file_path + key values, truncated
                    arg_preview = _fmt_args(args)
                    if arg_preview:
                        parts.append(f"{tc['name']}({arg_preview})")
                    else:
                        parts.append(tc["name"])
                seq_lines.append(f"    iter#{e['iteration']}: {' → '.join(parts)}")
            else:
                seq_lines.append(f"    iter#{e['iteration']}: (no tools)")
        seq_summary = "\n".join(seq_lines)

        # User negative signal aggregation
        all_signals: list[dict] = []
        for e in entries:
            sigs = e.get("user_signals", [])
            if isinstance(sigs, list):
                all_signals.extend(sigs)
        signal_counts = _aggregate_signals(all_signals)
        signal_str = ""
        if signal_counts:
            parts = [f"{k}={v}" for k, v in sorted(signal_counts.items())]
            signal_str = f"  User negative signals: {', '.join(parts)}\n"
        _write_user_corrections(all_signals)

        metrics_text = (
            f"  Iterations: {iteration_range} @ {time_str}\n"
            f"  Total iterations: {len(entries)}\n"
            f"  Total token usage: {total_tokens}\n"
            f"  Total tool calls: {total_tool_calls}\n"
            f"  Errors: {len(errors)}\n"
            f"{signal_str}"
            f"{freq_summary}\n"
            f"{file_summary}\n"
            f"{seq_summary}"
        )

        # Hook module structure (structural summary, not full source)
        hook_code = _summarize_hook_sources()

        # Call LLM for structured findings
        findings, diagnostic = await self._call_for_findings(metrics_text, hook_code)

        # Save findings JSON (consumed by SelfFixHook)
        self._save_findings(findings, iteration_range, time_str)

        # Write findings doc to workspace framework dir (for ContextBuilder injection)
        self._write_findings_doc(findings)

        # Also write readable log for human review
        self._append_to_log(iteration_range, time_str, errors, findings, diagnostic)

    async def _call_for_findings(self, metrics_text: str, hook_code: str) -> tuple[list[dict[str, Any]], str]:
        """Call LLM to extract task-relevant findings from this turn.

        Returns ``(findings, diagnostic)``.  *findings* is empty on failure;
        *diagnostic* explains why.
        """
        # Retry once on json_decode_error — LLM output is sometimes truncated mid-JSON.
        # The second attempt reuses the same LLM call; if it still fails we accept the
        # failure and return llm_empty so the session continues without crashing.
        for attempt in range(2):
            try:
                response = await self._raw_llm_call(metrics_text, hook_code)
                findings, diagnostic = self._parse_findings(response)
                if diagnostic != "json_decode_error":
                    return findings, diagnostic
                # fall through to retry on next iteration
            except Exception as exc:
                logger.debug("SelfDetectHook: LLM call failed: {}", exc)
                return [], "llm_call_error"
        # Second attempt also produced json_decode_error — treat as empty
        return [], "llm_empty"

    async def _raw_llm_call(self, metrics_text: str, hook_code: str) -> str:
        """Make a minimal LLM call and return the raw response string.

        Named `_raw_llm_call` (not `_call_llm`) to distinguish from `_call_for_findings`
        which is the actual interface used by the detection flow.
        """
        response = await chat_stream_with_retry(
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": REFLECTION_USER_TEMPLATE.format(
                    metrics_summary=metrics_text,
                    hook_code=hook_code,
                )},
            ],
        )
        if response.finish_reason == "error":
            return ""
        return response.content or ""

    @staticmethod
    def _parse_findings(raw: str) -> tuple[list[dict[str, Any]], str]:
        """Parse JSON findings from LLM response.

        Returns (findings, diagnostic_str) where:
          - findings: list of validated finding dicts, or [] on any failure
          - diagnostic_str: one of "ok" | "json_decode_error" | "empty_findings" |
                            "all_filtered" | "llm_empty", describing why parsing failed
        """
        if not raw or not raw.strip():
            return [], "llm_empty"

        # Use first ``` → last ``` to handle nested code fences inside JSON string values.
        idx = raw.find("```")
        if idx >= 0:
            content_start = raw.find("\n", idx)
            if content_start >= 0:
                content_start += 1
                fence_end = raw.rfind("```")
                if fence_end > content_start:
                    raw = raw[content_start:fence_end].strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return [], "json_decode_error"

        findings = result.get("findings", []) if isinstance(result, dict) else []
        if not isinstance(findings, list):
            return [], "json_decode_error"
        if not findings:
            return [], "empty_findings"

        valid = []
        for f in findings:
            if isinstance(f, dict) and f.get("type") and f.get("content"):
                ftype = f["type"]
                if ftype in ("knowledge", "decision", "behavior", "correction", "self_bug"):
                    valid.append(f)
        if not valid:
            return [], "all_filtered"
        return valid, "ok"

    @staticmethod
    def _finding_id(content: str) -> str:
        """Generate a stable finding ID from content hash."""
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _save_findings(
        self, findings: list[dict[str, Any]], iteration_range: str, time_str: str
    ) -> None:
        """Write structured findings to JSON file for SelfFixHook.

        Auto-resolves old findings that don't reappear: when new findings
        supersede a previous batch, the old IDs are written to
        resolved_findings.jsonl so self_findings.md stays fresh.
        """
        self.FINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        for f in findings:
            if "id" not in f and f.get("content"):
                f["id"] = self._finding_id(f["content"])

        # Auto-resolve stale findings from previous batch
        new_ids = {f["id"] for f in findings if f.get("id")}
        try:
            old_payload = json.loads(self.FINDINGS_FILE.read_text(encoding="utf-8"))
            old_findings = old_payload.get("findings", [])
            stale = [of.get("id") for of in old_findings
                     if of.get("id") and of["id"] not in new_ids]
            if stale:
                self.RESOLVED_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(self.RESOLVED_FILE, "a", encoding="utf-8") as f:
                    for oid in stale:
                        f.write(oid + "\n")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        # Read existing resolved IDs to carry forward
        resolved_ids = _read_resolved_ids()

        payload = {
            "saved_at": time_str,
            "iteration_range": iteration_range,
            "source": "self_detect",
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
        errors: list[dict],
        findings: list[dict[str, Any]],
        diagnostic: str,
    ) -> None:
        """Write human-readable reflection to markdown log.

        Always writes to log even when findings is empty — this ensures the file
        exists for external readers (e.g., daily-evolution cron) to analyze.
        When findings is empty, uses diagnostic to write a context-aware status
        instead of a generic "nothing actionable".
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
            diagnostic_msg = {
                "llm_empty": "(LLM returned empty response)",
                "json_decode_error": "(LLM output wasn't valid JSON)",
                "empty_findings": "(LLM returned no findings despite instruction)",
                "all_filtered": "(LLM returned findings but all were invalid format)",
                "llm_call_error": "(LLM call failed)",
            }.get(diagnostic, "(nothing actionable)")
            finding_lines = [diagnostic_msg]

        has_error = bool(errors)
        findings_text = "\n".join(finding_lines)
        header = (
            f"## Turn {iteration_range} -- {time_str}\n"
            f"> Status: {len(findings)} finding(s) | "
            f"{'error' if has_error else 'ok'} | "
            f"diagnostic: {diagnostic}\n"
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
