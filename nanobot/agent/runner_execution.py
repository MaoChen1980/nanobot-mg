"""Tool execution and batching for AgentRunner."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from nanobot.agent.runner_injection import drain_injections
from nanobot.agent.tools.ask import AskUserInterrupt
from nanobot.agent.runner_constants import _MAX_INJECTION_CYCLES
from nanobot.utils.runtime import repeated_external_lookup_error


def partition_tool_batches(
    spec: Any,
    tool_calls: list,
) -> list[list]:
    """Partition tool calls into batches respecting concurrency rules."""
    if not spec.concurrent_tools:
        return [[tool_call] for tool_call in tool_calls]

    batches: list[list] = []
    current: list = []
    for tool_call in tool_calls:
        get_tool = getattr(spec.tools, "get", None)
        tool = get_tool(tool_call.name) if callable(get_tool) else None
        can_batch = bool(tool and tool.concurrency_safe)
        if can_batch:
            current.append(tool_call)
            continue
        if current:
            batches.append(current)
            current = []
        batches.append([tool_call])
    if current:
        batches.append(current)
    return batches


async def execute_tools(
    self_ref: Any,
    spec: Any,
    tool_calls: list,
    external_lookup_counts: dict[str, int],
    messages: list[dict[str, Any]],
    injection_cycles: int,
    iteration: int,
) -> tuple:
    """Execute tool calls in batches.

    Returns (results, events, fatal_error, was_interrupted,
    new_injection_cycles, executed_count, saved_injections).
    """
    batches = partition_tool_batches(spec, tool_calls)
    tool_results: list = []
    interrupted = False
    saved_injections: list = []
    turn = 0

    for batch in batches:
        if spec.concurrent_tools and len(batch) > 1:
            batch_results = await _batch_run_tools(
                self_ref, spec, batch, external_lookup_counts, iteration, turn
            )
            turn += len(batch)
            tool_results.extend(batch_results)
        else:
            batch_results = []
            for tool_call in batch:
                result = await _run_tool(
                    self_ref, spec, tool_call, external_lookup_counts, iteration, turn
                )
                turn += 1
                tool_results.append(result)
                batch_results.append(result)
                if isinstance(result[2], AskUserInterrupt):
                    break
        if any(isinstance(error, AskUserInterrupt) for _, _, error in batch_results):
            break

        if tool_results and injection_cycles < _MAX_INJECTION_CYCLES:
            pending = await drain_injections(spec)
            if pending:
                injection_cycles += 1
                saved_injections = pending
                interrupted = True
                break

    results: list = []
    events: list = []
    fatal_error: BaseException | None = None
    for result, event, error in tool_results:
        results.append(result)
        events.append(event)
        if error is not None and fatal_error is None:
            fatal_error = error
    return results, events, fatal_error, interrupted, injection_cycles, len(tool_results), saved_injections


async def _run_tool(
    self_ref: Any,
    spec: Any,
    tool_call: Any,
    external_lookup_counts: dict[str, int],
    iteration: int,
    turn: int,
) -> tuple:
    """Execute a single tool call and return (result, event, error)."""
    lookup_error = repeated_external_lookup_error(
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
            pass
    if prep_error:
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": prep_error.split(": ", 1)[-1][:120],
        }
        return prep_error, event, RuntimeError(prep_error) if spec.fail_on_tool_error else None

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
        }
        if isinstance(exc, AskUserInterrupt):
            event["status"] = "waiting"
            result_str = ""
            error_str = str(exc)
        else:
            result_str = f"Error: {type(exc).__name__}: {exc}"
            error_str = result_str
        self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, result_str, False, error_str, duration_ms)
        if isinstance(exc, AskUserInterrupt):
            return "", event, exc
        if spec.fail_on_tool_error:
            return result_str, event, RuntimeError(str(exc))
        return result_str, event, None

    if isinstance(result, str) and result.startswith("Error"):
        event = {
            "name": tool_call.name,
            "status": "error",
            "detail": result.replace("\n", " ").strip()[:120],
        }
        self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, result, False, result, duration_ms)
        if spec.fail_on_tool_error:
            return result, event, RuntimeError(result)
        return result, event, None

    detail = "" if result is None else str(result)
    detail = detail.replace("\n", " ").strip()
    if not detail:
        detail = "(empty)"
    elif len(detail) > 120:
        detail = detail[:120] + "..."
    self_ref._log_tool_call(spec.session_key, iteration, turn, tool_call.name, tool_call.arguments, str(result) if result else "", True, None, duration_ms)
    return result, {"name": tool_call.name, "status": "ok", "detail": detail}, None


async def _batch_run_tools(
    self_ref: Any,
    spec: Any,
    tool_calls: list,
    external_lookup_counts: dict[str, int],
    iteration: int,
    turn_start: int,
) -> list:
    """Execute multiple tool calls concurrently."""
    tasks = [
        _run_tool(self_ref, spec, tc, external_lookup_counts, iteration, turn_start + i)
        for i, tc in enumerate(tool_calls)
    ]
    return await asyncio.gather(*tasks)