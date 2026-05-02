"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.context_vars import _current_messages_for_subagent

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.utils.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    maybe_persist_tool_result,
    truncate_text,
)
from nanobot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
)

# Import from split modules
from .runner_constants import (
    _BACKFILL_CONTENT,
    _COMPACTABLE_TOOLS,
    _DEFAULT_ERROR_MESSAGE,
    _MAX_EMPTY_RETRIES,
    _MAX_INJECTION_CYCLES,
    _MAX_INJECTIONS_PER_TURN,
    _MAX_LENGTH_RECOVERIES,
    _MICROCOMPACT_KEEP_RECENT,
    _MICROCOMPACT_MIN_CHARS,
    _PERSISTED_MODEL_ERROR_PLACEHOLDER,
    _SNIP_SAFETY_BUFFER,
)

# Re-export for backward compatibility
__all__ = [
    "AgentRunSpec", "AgentRunResult", "AgentRunner",
    "_BACKFILL_CONTENT", "_COMPACTABLE_TOOLS",
    "_MAX_EMPTY_RETRIES", "_MAX_INJECTION_CYCLES",
    "_MAX_INJECTIONS_PER_TURN", "_MAX_LENGTH_RECOVERIES",
    "_MICROCOMPACT_KEEP_RECENT", "_MICROCOMPACT_MIN_CHARS",
    "_PERSISTED_MODEL_ERROR_PLACEHOLDER", "_SNIP_SAFETY_BUFFER",
]
from .runner_context import (
    drop_orphan_tool_results,
    backfill_missing_tool_results,
    microcompact,
    apply_tool_result_budget,
    snip_history,
)
from .runner_injection import drain_injections, append_injected_messages
from .runner_llm import (
    request_model,
    request_finalization_retry,
    usage_dict,
    accumulate_usage,
    merge_usage,
)
from .runner_execution import execute_tools


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    workspace: Path | None = None
    session_key: str | None = None
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a shared agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self, provider: LLMProvider, db=None):
        self.provider = provider
        self._db = db

    async def _drain_injections(self, spec: AgentRunSpec) -> list[dict[str, Any]]:
        """Drain pending injections. Returns normalized user messages."""
        return await drain_injections(spec)

    async def _execute_tools(self, *args, **kwargs):
        """Backward compat wrapper — delegate to module function."""
        return await execute_tools(self, *args, **kwargs)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        assistant_message: dict[str, Any] | None,
        injection_cycles: int,
        *,
        phase: str = "after error",
        iteration: int | None = None,
    ) -> tuple[bool, int]:
        """Drain pending injections. Returns (should_continue, updated_cycles)."""
        if injection_cycles >= _MAX_INJECTION_CYCLES:
            return False, injection_cycles
        injections = await drain_injections(spec)
        if not injections:
            return False, injection_cycles
        injection_cycles += 1
        if assistant_message is not None and phase != "after final response":
            messages.append(assistant_message)
            if iteration is not None:
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                    },
                )
        append_injected_messages(messages, injections, assistant_message)
        logger.info(
            "Injected {} follow-up message(s) {} ({}/{})",
            len(injections), phase, injection_cycles, _MAX_INJECTION_CYCLES,
        )
        return True, injection_cycles

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        final_content: str | None = None
        tools_used: list[str] = []
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        error: str | None = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []
        external_lookup_counts: dict[str, int] = {}
        empty_content_retries = 0
        length_recovery_count = 0
        had_injections = False
        injection_cycles = 0

        _current_messages_for_subagent.set(messages)

        def _normalize(spec, tc_id, name, result):
            result = ensure_nonempty_tool_result(name, result)
            try:
                content = maybe_persist_tool_result(
                    spec.workspace, spec.session_key, tc_id, result,
                    max_chars=spec.max_tool_result_chars,
                )
            except Exception as exc:
                logger.warning(
                    "Tool result persist failed for {} in {}: {}; using raw result",
                    tc_id, spec.session_key or "default", exc,
                )
                content = result
            if isinstance(content, str) and len(content) > spec.max_tool_result_chars:
                return truncate_text(content, spec.max_tool_result_chars)
            return content

        for iteration in range(spec.max_iterations):
            try:
                messages_for_model = drop_orphan_tool_results(messages)
                messages_for_model = backfill_missing_tool_results(messages_for_model)
                messages_for_model = microcompact(messages_for_model)
                messages_for_model = apply_tool_result_budget(spec, messages_for_model, _normalize)
                messages_for_model = snip_history(self.provider, spec, messages_for_model)
                messages_for_model = drop_orphan_tool_results(messages_for_model)
                messages_for_model = backfill_missing_tool_results(messages_for_model)
            except Exception as exc:
                logger.warning(
                    "Context governance failed on turn {} for {}: {}; applying minimal repair",
                    iteration, spec.session_key or "default", exc,
                )
                try:
                    messages_for_model = drop_orphan_tool_results(messages)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                except Exception:
                    messages_for_model = messages

            context = AgentHookContext(iteration=iteration, messages=messages, workspace=spec.workspace)
            await hook.before_iteration(context)
            response = await request_model(self.provider, spec, messages_for_model, hook, context)
            raw_usage = usage_dict(response.usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            accumulate_usage(usage, raw_usage)

            if response.should_execute_tools:
                tool_calls = list(response.tool_calls)
                ask_index = next((i for i, tc in enumerate(tool_calls) if tc.name == "ask_user"), None)
                if ask_index is not None:
                    tool_calls = tool_calls[:ask_index + 1]
                context.tool_calls = list(tool_calls)
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                tools_used.extend(tc.name for tc in tool_calls)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [tc.to_openai_tool_call() for tc in tool_calls],
                    },
                )

                await hook.before_execute_tools(context)

                (results, new_events, fatal_error, was_interrupted,
                 injection_cycles, executed_count, saved_injections) = await execute_tools(
                    self, spec, tool_calls, external_lookup_counts, messages,
                    injection_cycles, iteration,
                )
                tool_events.extend(new_events)
                context.tool_results = list(results)
                context.tool_events = list(new_events)

                if was_interrupted:
                    completed_tool_results: list[dict[str, Any]] = []
                    for i, tc in enumerate(tool_calls):
                        if i < executed_count:
                            res = results[i]
                            content = _normalize(spec, tc.id, tc.name, res)
                            ts = res.timestamp.isoformat() if hasattr(res, "timestamp") and res.timestamp else ""
                            content = self._fmt_tool_metadata(tc.name, content, ts)
                        else:
                            content = f"[ABANDONED] tool call {tc.name} was not executed due to interruption"
                            ts = ""
                        tool_message = {
                            "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                            "content": content, "timestamp": ts,
                        }
                        messages.append(tool_message)
                        completed_tool_results.append(tool_message)
                    append_injected_messages(messages, saved_injections, None)
                    logger.info(
                        "Injected {} saved follow-up message(s) after tool results ({}/{})",
                        len(saved_injections), injection_cycles, _MAX_INJECTION_CYCLES,
                    )
                    empty_content_retries = 0
                    length_recovery_count = 0
                    had_injections = True
                    await hook.after_iteration(context)
                    continue

                completed_tool_results = []
                for tool_call, result in zip(tool_calls, results):
                    from nanobot.agent.tools.ask import AskUserInterrupt
                    if isinstance(fatal_error, AskUserInterrupt) and tool_call.name == "ask_user":
                        continue
                    content = _normalize(spec, tool_call.id, tool_call.name, result)
                    ts = result.timestamp.isoformat() if hasattr(result, "timestamp") and result.timestamp else ""
                    content = self._fmt_tool_metadata(tool_call.name, content, ts)
                    tool_message = {
                        "role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name,
                        "content": content, "timestamp": ts,
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)

                if fatal_error is not None:
                    from nanobot.agent.tools.ask import AskUserInterrupt
                    if isinstance(fatal_error, AskUserInterrupt):
                        final_content = fatal_error.question
                        stop_reason = "ask_user"
                        context.final_content = final_content
                        context.stop_reason = stop_reason
                        if hook.wants_streaming():
                            await hook.on_stream_end(context, resuming=False)
                        await hook.after_iteration(context)
                        break
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    should_continue, injection_cycles = await self._try_drain_injections(
                        spec, messages, None, injection_cycles, phase="after tool error",
                    )
                    if should_continue:
                        had_injections = True
                        continue
                    break

                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "tools_completed",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": completed_tool_results,
                        "pending_tool_calls": [],
                    },
                )
                empty_content_retries = 0
                length_recovery_count = 0
                await hook.after_iteration(context)
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason, spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)
            if response.finish_reason != "error" and is_blank_text(clean):
                empty_content_retries += 1
                if empty_content_retries < _MAX_EMPTY_RETRIES:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        iteration, spec.session_key or "default",
                        empty_content_retries, _MAX_EMPTY_RETRIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration, spec.session_key or "default", empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                response = await request_finalization_retry(self.provider, spec, messages_for_model)
                retry_usage = usage_dict(response.usage)
                accumulate_usage(usage, retry_usage)
                raw_usage = merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = dict(raw_usage)
                context.tool_calls = list(response.tool_calls)
                clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration, spec.session_key or "default",
                        length_recovery_count, _MAX_LENGTH_RECOVERIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=True)
                    messages.append(build_assistant_message(
                        clean,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    ))
                    messages.append(build_length_recovery_message())
                    await hook.after_iteration(context)
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

            should_continue, injection_cycles = await self._try_drain_injections(
                spec, messages, assistant_message, injection_cycles,
                phase="after final response", iteration=iteration,
            )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles, phase="after LLM error",
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            if is_blank_text(clean):
                final_content = EMPTY_FINAL_RESPONSE_MESSAGE
                stop_reason = "empty_final_response"
                error = final_content
                self._append_final_message(messages, final_content)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles, phase="after empty response",
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            messages.append(assistant_message or build_assistant_message(
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            ))
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": messages[-1],
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                },
            )
            final_content = clean
            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)
            break

        else:
            stop_reason = "max_iterations"
            if spec.max_iterations_message:
                final_content = spec.max_iterations_message.format(max_iterations=spec.max_iterations)
            else:
                from nanobot.utils.prompt_templates import render_template
                final_content = render_template(
                    "agent/max_iterations_message.md",
                    strip=True,
                    max_iterations=spec.max_iterations,
                )
            self._append_final_message(messages, final_content)
            drained_after_max_iterations, injection_cycles = await self._try_drain_injections(
                spec, messages, None, injection_cycles, phase="after max_iterations",
            )
            if drained_after_max_iterations:
                had_injections = True

        return AgentRunResult(
            final_content=final_content,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=error,
            tool_events=tool_events,
            had_injections=had_injections,
        )

    def _log_tool_call(
        self, session_key: str, iteration: int, turn: int,
        tool_name: str, params: dict[str, Any] | None,
        result: str, success: bool, error: str | None, duration_ms: int | None = None,
    ) -> None:
        if self._db is None:
            return
        try:
            self._db.insert_tool_call(
                session_key=session_key, iteration=iteration, turn=turn,
                tool_name=tool_name, params=params, result=result,
                success=success, error=error,
            )
        except Exception:
            pass

    async def _emit_checkpoint(self, spec: AgentRunSpec, payload: dict[str, Any]) -> None:
        if spec.checkpoint_callback is not None:
            await spec.checkpoint_callback(payload)

    @staticmethod
    def _append_final_message(messages: list[dict[str, Any]], content: str | None) -> None:
        if not content:
            return
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    @staticmethod
    def _fmt_tool_metadata(tool_name: str, result: str, timestamp: str = "") -> str:
        """Prefix tool result with searchable metadata: tool name, size, time."""
        size = len(result) if isinstance(result, str) else 0
        ts = timestamp[:16].replace("T", " ") if timestamp else ""
        meta = f"[Tool: {tool_name}"
        if ts:
            meta += f" | {ts}"
        meta += f" | {size} chars]"
        return f"{meta}\n{result}"

    # Backward compatibility — delegate to module functions
    _drop_orphan_tool_results = staticmethod(drop_orphan_tool_results)
    _backfill_missing_tool_results = staticmethod(backfill_missing_tool_results)
    _microcompact = staticmethod(microcompact)
    _apply_tool_result_budget = staticmethod(apply_tool_result_budget)
    _snip_history = lambda self, spec, msgs: snip_history(self.provider, spec, msgs)