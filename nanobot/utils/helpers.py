"""Utility functions for nanobot."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import tiktoken
from loguru import logger

# Lazy-init cached tiktoken encoder — creation is expensive (~50-100ms)
_cached_encoder: Any = None


def _get_encoder():
    global _cached_encoder
    if _cached_encoder is None:
        _cached_encoder = tiktoken.get_encoding("cl100k_base")
    return _cached_encoder


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp() -> str:
    """Current ISO timestamp with timezone."""
    return datetime.now().astimezone().isoformat()


def format_message_header() -> str:
    return f"====== Message Time: {datetime.now().astimezone().isoformat()} ======"


def current_time_str(timezone: str | None = None) -> str:
    """Return the current time in LLM-friendly format."""
    from zoneinfo import ZoneInfo

    if timezone is None:
        now = datetime.now().astimezone()
    else:
        try:
            tz = ZoneInfo(timezone) if timezone else None
        except Exception:
            tz = None
        now = datetime.now(tz=tz) if tz else datetime.now().astimezone()

    return _format_datetime(now)


def _format_datetime(dt: datetime) -> str:
    """Format a timezone-aware datetime as 'YYYY-MM-DD HH:MM:SS (Name, UTC±HH:MM)'."""
    base = dt.strftime("%Y-%m-%d %H:%M:%S")
    tz_name = getattr(dt.tzinfo, "key", "")
    offset = dt.utcoffset()
    if offset is not None:
        total_min = int(offset.total_seconds() / 60)
        sign = "+" if total_min >= 0 else "-"
        h, m = divmod(abs(total_min), 60)
        offset_str = f"UTC{sign}{h:02d}:{m:02d}"
        if tz_name:
            return f"{base} ({tz_name}, {offset_str})"
        return f"{base} ({offset_str})"
    return base


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = "tool-results"
_TOOL_RESULT_RETENTION_SECS = 7 * 24 * 60 * 60
_TOOL_RESULT_MAX_BUCKETS = 32


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a stable suffix."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
) -> str:
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        result += "\n...\n(Read the saved file if you need the full output.)"
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    siblings = [path for path in root.iterdir() if path.is_dir() and path != current_bucket]
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in siblings:
        if _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
    keep = max(_TOOL_RESULT_MAX_BUCKETS - 1, 0)
    siblings = [path for path in siblings if path.exists()]
    if len(siblings) <= keep:
        return
    siblings.sort(key=_bucket_mtime, reverse=True)
    for path in siblings[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        from nanobot.utils.document import stringify_text_blocks
        text_payload = stringify_text_blocks(content)
        if text_payload is None:
            return content
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return content

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception as exc:
        logger.warning("Failed to clean stale tool result buckets in {}: {}", root, exc)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    if not path.exists():
        if suffix == "json" and isinstance(content, list):
            _write_text_atomic(path, json.dumps(content, ensure_ascii=False, indent=2))
        else:
            _write_text_atomic(path, text_payload)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
    )


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    reasoning_details: list[dict] | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None or thinking_blocks:
        msg["reasoning_content"] = reasoning_content if reasoning_content is not None else ""
    if reasoning_details is not None:
        msg["reasoning_details"] = reasoning_details
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate prompt tokens with tiktoken.

    Counts all fields that providers send to the LLM: content, tool_calls,
    reasoning_content, tool_call_id, name, plus per-message framing overhead.
    """
    try:
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)

            tc = msg.get("tool_calls")
            if tc:
                parts.append(json.dumps(tc, ensure_ascii=False))

            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc:
                parts.append(rc)

            for key in ("name", "tool_call_id"):
                value = msg.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)

        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))

        per_message_overhead = len(messages) * 4
        return len(_get_encoder().encode("\n".join(parts))) + per_message_overhead
    except Exception:
        logger.debug("Token estimation failed")
        return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)

    tb = message.get("thinking_blocks")
    if isinstance(tb, list):
        for block in tb:
            text = block.get("thinking") if isinstance(block, dict) else None
            if text:
                parts.append(text)

    payload = "\n".join(parts)
    if not payload:
        return 4
    try:
        enc = _get_encoder()
        return max(4, len(enc.encode(payload)) + 4)
    except Exception:
        logger.debug("Message token estimation failed")
        return max(4, len(payload) // 4 + 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider counter first, then tiktoken fallback."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        try:
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception:
            logger.debug("Provider token counter failed")

    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "tiktoken"
    return 0, "none"


def build_status_content(
    *,
    version: str,
    model: str,
    start_time: float,
    last_usage: dict[str, int],
    context_window_tokens: int,
    session_msg_count: int,
    context_tokens_estimate: int,
    search_usage_text: str | None = None,
    active_task_count: int = 0,
    max_completion_tokens: int = 8192,
) -> str:
    """Build a human-readable runtime status snapshot.

    Args:
        search_usage_text: Optional pre-formatted web search usage string
                           (produced by SearchUsageInfo.format()). When provided
                           it is appended as an extra section.
    """
    uptime_s = int(time.time() - start_time)
    uptime = (
        f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m"
        if uptime_s >= 3600
        else f"{uptime_s // 60}m {uptime_s % 60}s"
    )
    last_in = last_usage.get("prompt_tokens", 0)
    last_out = last_usage.get("completion_tokens", 0)
    cached = last_usage.get("cached_tokens", 0)
    ctx_total = max(context_window_tokens, 0)
    # Budget: ctx_window - max_completion - _SAFETY_BUFFER
    ctx_budget = max(ctx_total - int(max_completion_tokens) - 1024, 1)
    ctx_pct = min(int((context_tokens_estimate / ctx_budget) * 100), 999) if ctx_budget > 0 else 0
    ctx_used_str = (
        f"{context_tokens_estimate // 1000}k"
        if context_tokens_estimate >= 1000
        else str(context_tokens_estimate)
    )
    ctx_total_str = f"{ctx_total // 1000}k" if ctx_total > 0 else "n/a"
    token_line = f"**Tokens:** {last_in} in / {last_out} out"
    if cached and last_in:
        token_line += f" ({cached * 100 // last_in}% cached)"
    lines = [
        f"## \U0001f408 nanobot v{version}",
        f"**Model:** {model}",
        token_line,
        f"**Context:** {ctx_used_str}/{ctx_total_str} ({ctx_pct}% of input budget)",
        f"**Session:** {session_msg_count} messages",
        f"**Uptime:** {uptime}",
        f"**Tasks:** {active_task_count} active",
    ]
    if search_usage_text:
        lines.append(search_usage_text)
    return "\n".join(lines)


def split_thinking_messages(messages: list[dict]) -> list[dict]:
    """Split assistant messages with thinking/reasoning into separate messages.

    When an assistant message contains both thinking/reasoning and tool calls
    (or regular content), split it into two messages so the LLM doesn't
    confuse thinking text with tool call structure during history parsing.
    """
    result: list[dict] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            result.append(msg)
            continue

        thinking = None

        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc.strip():
            thinking = rc.strip()

        if not thinking:
            blocks = msg.get("thinking_blocks")
            if isinstance(blocks, list):
                texts = [b.get("thinking", "") for b in blocks if isinstance(b, dict) and b.get("thinking")]
                if texts:
                    thinking = " ".join(texts)

        if not thinking:
            rd = msg.get("reasoning_details")
            if isinstance(rd, list):
                texts = [d.get("reasoning", "") for d in rd if isinstance(d, dict) and d.get("reasoning")]
                if texts:
                    thinking = " ".join(texts)

        if not thinking:
            result.append(msg)
            continue

        content = msg.get("content", "")
        content_is_backfilled = (
            isinstance(content, str)
            and content.strip()
            and content.strip() == thinking
        )

        has_tool_calls = bool(msg.get("tool_calls"))
        has_real_content = (
            isinstance(content, str)
            and content.strip()
            and not content_is_backfilled
        )
        has_content_list = isinstance(content, list) and bool(content)

        if has_tool_calls or has_real_content or has_content_list:
            msg_think: dict = {"role": "assistant", "content": thinking}
            msg_rest: dict = {
                k: v for k, v in msg.items()
                if k not in ("reasoning_content", "reasoning_details", "thinking_blocks")
            }
            if has_tool_calls:
                msg_rest["content"] = ""
            result.append(msg_think)
            result.append(msg_rest)
        else:
            cleaned = dict(msg)
            for k in ("reasoning_content", "reasoning_details", "thinking_blocks"):
                cleaned.pop(k, None)
            cleaned["content"] = thinking
            result.append(cleaned)

    return result


