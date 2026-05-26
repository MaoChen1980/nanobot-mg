"""Structured progress-event helpers shared by agent runtimes."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger


# ----------------------------------------------------------------------
# Observe event emission — /think and /tool
# ----------------------------------------------------------------------


async def _emit_observe_event(event_type: str, content: str, metadata: dict | None = None) -> None:
    """Emit a /think or /tool observe event to the proxy channel via context var.

    Safe to call even when no loop is active (no-op).
    """
    from nanobot.agent.context_vars import _current_agent_loop

    loop = _current_agent_loop.get()
    if loop is not None:
        await loop._emit_observe_event(event_type, content, metadata or {})


# ----------------------------------------------------------------------
# on_progress integration (legacy callback format)
# ----------------------------------------------------------------------


def on_progress_accepts_tool_events(on_progress: Callable[..., Any] | None) -> bool:
    """Check whether on_progress callback accepts a ``tool_events`` kwarg.

    Returns True if the callback has ``tool_events`` as a named parameter
    (positional or keyword), accepts ``**kwargs``, or has 3+ positional
    params (legacy ``cb(content, tool_hint, tool_events)`` format).
    """
    if on_progress is None:
        return False
    try:
        sig = inspect.signature(on_progress)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    # Named ``tool_events`` param (positional or keyword) or **kwargs
    if "tool_events" in params:
        return True
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    # 3+ positional params (legacy cb(content, tool_hint, tool_events))
    positional = [p for p in params.values() if p.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    )]
    return len(positional) >= 3


async def process_tool_events_and_progress(
    on_progress: Callable[..., Awaitable[None] | None] | None,
    content: str,
    tool_hint: bool = False,
    tool_events: Any = None,
    **kwargs: Any,
) -> None:
    """Call on_progress with structured args (legacy format: content + tool_hint + tool_events).

    Also emits via the new _emit_observe_event context var when available.
    """
    # Determine whether to pass tool_events to the old callback.
    accept_tool_events = on_progress_accepts_tool_events(on_progress)

    # Emit via the new observe system (_emit_observe_event is always safe to call).
    if tool_events:
        for te in tool_events:
            if isinstance(te, dict):
                phase = te.get("phase", "start")
                if phase == "start":
                    await _emit_observe_event("tool_start", content, te)
                elif phase == "end":
                    await _emit_observe_event("tool_end", content, te)
                elif phase == "error":
                    await _emit_observe_event("tool_error", content, te)

    if on_progress is not None:
        try:
            # Only pass tool_events to old-format callbacks that can accept them.
            passed_tool_events = tool_events if accept_tool_events else None
            result = on_progress(content, tool_hint=tool_hint, tool_events=passed_tool_events, **kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("on_progress callback raised: {}", exc)


# ----------------------------------------------------------------------
# Legacy payload format (kept for backwards compat with existing tests)
# ----------------------------------------------------------------------


def tool_event_result_extras(result: Any) -> tuple[list[str], list[dict]]:
    """Extract file/embed extras from a tool result dict (legacy compat)."""
    files: list[str] = []
    embeds: list[dict] = []
    if isinstance(result, dict):
        if isinstance(result.get("files"), list):
            files = result["files"]
        if isinstance(result.get("embeds"), list):
            embeds = result["embeds"]
    return files, embeds


def build_tool_event_start_payload(tc: Any) -> dict:
    """Build a dict-based start event payload (legacy compat)."""
    call_id = getattr(tc, "id", "")
    name = getattr(tc, "name", "")
    arguments = getattr(tc, "arguments", {})
    if isinstance(arguments, str):
        import json
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    return {
        "version": 1,
        "phase": "start",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "result": None,
        "error": None,
        "files": [],
        "embeds": [],
    }


def build_tool_event_finish_payloads(context: Any) -> list[dict]:
    """Build dict-based finish event payloads from context (legacy compat)."""
    payloads = []
    tool_calls = getattr(context, "tool_calls", [])
    results = getattr(context, "tool_results", [])
    events = getattr(context, "tool_events", [])
    n = min(len(tool_calls), len(results), len(events))
    for i in range(n):
        tc = tool_calls[i]
        result = results[i]
        event = events[i]
        call_id = getattr(tc, "id", "")
        name = getattr(tc, "name", "")
        arguments = getattr(tc, "arguments", {}) or {}
        if isinstance(arguments, str):
            import json
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        status = event.get("status", "ok") if isinstance(event, dict) else "ok"
        files, embeds = tool_event_result_extras(result)
        if status == "error":
            error_detail = event.get("detail") if isinstance(event, dict) else None
            error_msg = error_detail or (result if isinstance(result, str) and result else "Tool execution failed")
            payloads.append({
                "version": 1, "phase": "error",
                "call_id": call_id, "name": name,
                "arguments": arguments, "result": None,
                "error": error_msg,
                "files": [], "embeds": [],
            })
        else:
            payloads.append({
                "version": 1, "phase": "end",
                "call_id": call_id, "name": name,
                "arguments": arguments, "result": result,
                "error": None,
                "files": files, "embeds": embeds,
            })
    return payloads