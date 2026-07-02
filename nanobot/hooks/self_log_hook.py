"""
SelfLogHook: lightweight per-iteration metrics logger.

Phase 1: minimal data capture — logs metrics to self_review_log.jsonl.
Phase 2 (SelfDetectHook) adds LLM suspect detection.
Phase 3 (SelfFixHook) injects findings back into agent context.

Together they form the self-evolution feedback loop:
  SelfLogHook (log) → SelfDetectHook (detect) → SelfFixHook (fix)

Log file: ~/.nanobot/self_improve/self_review_log.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

try:
    import msvcrt as _msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False


def _lock_file(f, exclusive: bool = True) -> None:
    """Acquire an advisory lock on *f*."""
    if _HAS_FCNTL:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH)
    elif _HAS_MSVCRT:
        _msvcrt.locking(f.fileno(), _msvcrt.LK_NBLCK, 1)


def _unlock_file(f) -> None:
    """Release an advisory lock on *f*."""
    if _HAS_FCNTL:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
    elif _HAS_MSVCRT:
        _msvcrt.locking(f.fileno(), _msvcrt.LK_UNLCK, 1)


class SelfLogHook(AgentHook):
    """Lightweight metrics logger that runs after each iteration.

    Captures: tool call counts, errors, empty results, usage stats.
    No LLM calls — all captured from hook context.
    Phase 2 (SelfDetectHook) and Phase 3 (SelfFixHook) complete the loop.
    """

    LOG_FILE = Path.home() / ".nanobot" / "self_improve" / "self_review_log.jsonl"

    # Patterns that count as "discomfort" signals (word-bounded regex to avoid false positives)
    DISCOMFORT_PATTERNS: list[tuple[str, re.Pattern]] = [
        ("error", re.compile(r"\b[a-zA-Z]*[Ee]rror\b")),  # catches both "error" and "ValueError"
        ("failed", re.compile(r"\bfailed\b", re.IGNORECASE)),
        ("not found", re.compile(r"\bnot found\b", re.IGNORECASE)),
        ("permission denied", re.compile(r"\bpermission denied\b", re.IGNORECASE)),
        ("timeout", re.compile(r"\btimeout\b", re.IGNORECASE)),
        ("empty result", re.compile(r"\bempty result\b", re.IGNORECASE)),
        ("no such file", re.compile(r"\bno such file\b", re.IGNORECASE)),
        ("does not exist", re.compile(r"\bdoes not exist\b", re.IGNORECASE)),
    ]

    async def after_iteration(self, context: AgentHookContext) -> None:
        try:
            self._capture(context)
        except Exception:
            logger.warning("SelfLogHook.after_iteration failed", exc_info=True)

    def _capture(self, context: AgentHookContext) -> None:
        # Build basic metrics from context
        tool_count = len(context.tool_calls)
        error_count = sum(
            1
            for r in (context.tool_results or [])
            if self._is_error_result(r)
        )
        empty_result_count = sum(
            1
            for r in (context.tool_results or [])
            if self._is_empty_result(r)
        )

        # Count discomfort signals in tool results (with tool name for precision)
        discomfort_signals = []
        for i, r in enumerate(context.tool_results or []):
            signal = self._detect_discomfort(r)
            if signal:
                tool_name = ""
                if context.tool_calls and i < len(context.tool_calls):
                    tool_name = context.tool_calls[i].name
                discomfort_signals.append({"pattern": signal, "tool": tool_name})

        # Basic usage stats
        usage = context.usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        # Aggregate duration from tool_events (event dicts contain duration_ms, not tool_results)
        # tool_results = raw tool return values; tool_events = {"name", "status", "detail", "duration_ms"}
        duration_sec = 0.0
        for ev in context.tool_events or []:
            if isinstance(ev, dict) and ev.get("duration_ms"):
                duration_sec += ev["duration_ms"] / 1000.0
            elif hasattr(ev, "duration_ms") and ev.duration_ms:
                duration_sec += ev.duration_ms / 1000.0

        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iteration": context.iteration,
            "tool_count": tool_count,
            "tool_names": [tc.name for tc in (context.tool_calls or [])],
            "error_count": error_count,
            "empty_result_count": empty_result_count,
            "discomfort_signals": discomfort_signals,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_sec": round(duration_sec, 3),
            "has_error": context.error is not None,
            "has_final_content": context.final_content is not None,
            "message_count": len(context.messages),
        }

        self._append_log(entry)

    def _is_error_result(self, result: object) -> bool:
        """Check if a tool result is an actual error (not a successful message mentioning error).

        Only returns True when the structured ``status`` field is "fail" or "error".
        Substring matching on result text is unreliable — it matches "no errors found"
        and other benign messages.
        """
        if result is None:
            return False
        if isinstance(result, dict):
            status = result.get("status")
            if status in ("fail", "error"):
                return True
            error_field = result.get("error")
            if error_field and error_field is not None and str(error_field).strip():
                return True
            return False
        return False

    def _is_empty_result(self, result: object) -> bool:
        """Check if a tool result is empty or null."""
        if result is None:
            return True
        if isinstance(result, dict):
            if not any(v is not None for v in result.values()):
                return True
            res = result.get("result")
            if res is not None:
                s = str(res).strip()
                return s in ("", "None", "[]", "{}", "null")
            return False
        s = str(result).strip()
        return s in ("", "None", "[]", "{}", "null")

    def _detect_discomfort(self, result: object) -> str | None:
        """Detect a discomfort signal — only on actual failures.

        Renamed semantics: only count when ``status`` indicates a real failure.
        Successful messages that happen to mention "error" / "permission" / etc.
        (e.g. grep output of "no error found") are NOT discomfort.

        Plain string inputs without status info are skipped (cannot confirm
        failure), to avoid double-counting with substring patterns.
        """
        if result is None:
            return None
        if isinstance(result, dict):
            status = result.get("status")
            if status not in ("fail", "error"):
                return None
        else:
            # Non-dict inputs (e.g. legacy plain strings) lack failure status.
            # Treat as non-failure to keep semantics aligned with structured path.
            return None
        s = str(result)
        for name, pattern in self.DISCOMFORT_PATTERNS:
            if pattern.search(s):
                return name
        return None

    MAX_LOG_AGE_SECONDS = 86400  # 1 day
    MAX_LOG_LINES = 10000
    _rotate_counter = 0

    def _append_log(self, entry: dict) -> None:
        """Append one JSON line to the log file. Purges entries older than 1 day."""
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            _lock_file(f, exclusive=True)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                _unlock_file(f)
        self._rotate_counter += 1
        if self._rotate_counter % 10 == 0:
            self._maybe_rotate()

    @staticmethod
    def _filter_log_lines(lines: list[str], cutoff: float) -> list[str]:
        """Filter log lines by cutoff timestamp, keep unparsable lines."""
        kept: list[str] = []
        for line in lines:
            try:
                entry = json.loads(line)
                ts = entry.get("time", "")
                dt = datetime.fromisoformat(ts)
                if dt.timestamp() >= cutoff:
                    kept.append(line)
            except (ValueError, KeyError, TypeError):
                kept.append(line)
        return kept

    def _maybe_rotate(self) -> None:
        """Purge old entries. Exclusive lock held across read+filter+write to avoid races."""
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - self.MAX_LOG_AGE_SECONDS
        try:
            with open(self.LOG_FILE, "r+", encoding="utf-8") as f:
                _lock_file(f, exclusive=True)
                try:
                    lines = f.readlines()
                    if not lines:
                        return
                    kept = self._filter_log_lines(lines, cutoff)
                    if len(kept) > self.MAX_LOG_LINES:
                        kept = kept[-self.MAX_LOG_LINES // 2:]
                    if len(kept) == len(lines):
                        return
                    f.seek(0)
                    f.writelines(kept)
                    f.truncate()
                finally:
                    _unlock_file(f)
        except OSError:
            return

