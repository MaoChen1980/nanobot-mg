"""Tool execution for AgentRunner — sequential and parallel."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from nanobot.agent.runner_injection import drain_injections
from nanobot.agent.runner_constants import _MAX_INJECTION_CYCLES
from nanobot.agent.tools import file_state
from nanobot.utils.runtime import check_repeated_external_lookup


def _get_tool(spec: Any, name: str) -> Any | None:
    """Look up a tool by name from spec.tools, returning None if not found."""
    try:
        if spec is not None and hasattr(spec, "tools"):
            registry = spec.tools
            if hasattr(registry, "get"):
                return registry.get(name)
    except Exception:
        logger.exception("Tool lookup failed for '{}'", name)
    return None


def partition_tool_batches(
    spec: Any,
    tool_calls: list,
) -> list[list]:
    """Split tool calls into execution batches.

    Sequential mode (default): each tool gets its own batch → one at a time.
    Concurrent mode: consecutive non-exclusive tools are grouped into a single
    batch for parallel execution; exclusive tools get solo batches.
    """
    concurrent = getattr(spec, "concurrent_tools", False) if spec is not None else False
    if isinstance(concurrent, bool) and concurrent:
        batches: list[list] = []
        current: list = []
        for tc in tool_calls:
            tool = _get_tool(spec, tc.name)
            if tool is not None and getattr(tool, "exclusive", False):
                if current:
                    batches.append(current)
                    current = []
                batches.append([tc])
            else:
                current.append(tc)
        if current:
            batches.append(current)
        return batches
    return [[tool_call] for tool_call in tool_calls]


def _is_suppress_active(spec: Any, messages: list[dict[str, Any]]) -> bool:
    """Check if assess_me suppress output marker is active in recent messages."""
    try:
        from nanobot.agent.assess_me import contains_suppress_output_marker
        # Check last few messages for suppress marker (user role = assess_me inject)
        for m in reversed(messages[-6:]):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                if contains_suppress_output_marker(m.get("content", "")):
                    return True
        return False
    except Exception:
        return False


async def execute_tools(
    self_ref: Any,
    spec: Any,
    tool_calls: list,
    external_lookup_counts: dict[str, int],
    messages: list[dict[str, Any]],
    injection_cycles: int,
    iteration: int,
) -> tuple:
    """Execute tool calls sequentially.

    Returns (results, events, fatal_error, was_interrupted,
    new_injection_cycles, executed_count, saved_injections).
    """
    # Check if assess_me suppress phase is active
    suppress_tool_names: tuple = getattr(spec, "suppress_tool_names", ())
    suppressed_tool_results: list = []
    tool_calls_to_run: list = []

    if suppress_tool_names and _is_suppress_active(spec, messages):
        for tc in tool_calls:
            if tc.name in suppress_tool_names:
                # Return a suppressed result for blocked tools
                suppressed_result = (
                    "[suppressed] Tool blocked by assess_me suppress phase — "
                    f"'{tc.name}' may not be called during zero-output suppression.",
                    {"name": tc.name, "status": "suppressed", "detail": "blocked by suppress phase", "duration_ms": 0},
                    None,
                )
                suppressed_tool_results.append(suppressed_result)
            else:
                tool_calls_to_run.append(tc)
        logger.info("Assess_me suppress active: {} tool(s) blocked, {} tool(s) allowed",
                    len(suppressed_tool_results), len(tool_calls_to_run))
        tool_calls = tool_calls_to_run

    batches = partition_tool_batches(spec, tool_calls)
    tool_results: list = []
    interrupted = False
    saved_injections: list = []
    turn = 0

    for batch in batches:
        batch_results = []
        if len(batch) > 1 and spec.concurrent_tools:
            # Parallel execution
            turns = list(range(turn, turn + len(batch)))
            coros = [
                _run_tool(self_ref, spec, tc, external_lookup_counts, iteration, t)
                for tc, t in zip(batch, turns)
            ]
            results = await asyncio.gather(*coros)
            turn += len(batch)
            for r in results:
                tool_results.append(r)
                batch_results.append(r)
            if any(isinstance(r[2], RuntimeError) for r in results):
                interrupted = True
                break
        else:
            # Sequential execution
            for tool_call in batch:
                result = await _run_tool(
                    self_ref, spec, tool_call, external_lookup_counts, iteration, turn
                )
                turn += 1
                tool_results.append(result)
                batch_results.append(result)
                if isinstance(result[2], RuntimeError):
                    interrupted = True
                    break
            if any(isinstance(error, RuntimeError) for _, _, error in batch_results):
                interrupted = True
                break

        if tool_results and injection_cycles < _MAX_INJECTION_CYCLES:
            pending = await drain_injections(spec)
            if pending:
                injection_cycles += 1
                saved_injections = pending
                interrupted = True
                break

    # Prepend any suppressed tool results so the full tool_calls list matches in runner.py
    all_tool_results = suppressed_tool_results + tool_results
    results: list = []
    events: list = []
    fatal_error: BaseException | None = None
    for result, event, error in all_tool_results:
        results.append(result)
        events.append(event)
        if error is not None and fatal_error is None:
            fatal_error = error
    return results, events, fatal_error, interrupted, injection_cycles, len(all_tool_results), saved_injections


async def _run_tool(
    self_ref: Any,
    spec: Any,
    tool_call: Any,
    external_lookup_counts: dict[str, int],
    iteration: int,
    turn: int,
) -> tuple:
    """Execute a single tool call and return (result, event, error)."""
    if spec is not None and hasattr(spec, "session_key"):
        file_state._current_session_key.set(spec.session_key)
    lookup_error = check_repeated_external_lookup(
        tool_call.name,
        tool_call.arguments,
        external_lookup_counts,
    )
    if lookup_error:
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": "repeated external lookup blocked",
        }
        if spec.fail_on_tool_error:
            return lookup_error, event, RuntimeError(lookup_error)
        return lookup_error, event, None

    prepare_call = getattr(spec.tools, "prepare_call", None)
    tool, params, prep_error = None, tool_call.arguments, None
    if callable(prepare_call):
        try:
            prepared = prepare_call(tool_call.name, tool_call.arguments)
            if isinstance(prepared, tuple) and len(prepared) == 3:
                tool, params, prep_error = prepared
        except Exception:
            logger.exception("prepare_call failed for tool '{}'", tool_call.name)
            pass
    if prep_error:
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": prep_error.split(": ", 1)[-1][:120],
        }
        if spec.fail_on_tool_error:
            return prep_error, event, RuntimeError(prep_error)
        return prep_error, event, None

    start = time.monotonic()
    try:
        if tool is not None:
            result = await tool.execute(**params)
        else:
            result = await spec.tools.execute(tool_call.name, params)
        duration_ms = int((time.monotonic() - start) * 1000)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": str(exc),
            "duration_ms": duration_ms,
        }
        result_str = f"Error: {type(exc).__name__}: {exc}"
        error_str = result_str
        self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, result_str, False, error_str, duration_ms)
        if spec.fail_on_tool_error:
            return result_str, event, RuntimeError(str(exc))
        return result_str, event, None

    if isinstance(result, str) and result.startswith("Error"):
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": result.replace("\n", " ").strip()[:120],
            "duration_ms": duration_ms,
        }
        self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, result, False, result, duration_ms)
        if spec.fail_on_tool_error:
            return result, event, RuntimeError(result)
        return result, event, None

    detail = "" if result is None else str(result)
    detail_raw = detail
    detail = detail.replace("\n", " ").strip()
    if not detail:
        detail = "(empty)"
    elif len(detail) > 120:
        detail = detail[:120] + "..."
    self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, detail_raw, True, None, duration_ms)
    return result, {"name": tool_call.name, "status": "ok", "detail": detail, "duration_ms": duration_ms}, None