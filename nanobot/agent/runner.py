"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import json
import time
from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.assess_me import assess_me as _run_assess_me
from nanobot.agent.assess_me import build_assessment_message
from nanobot.agent.context_vars import _current_messages_for_subagent
from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session
from nanobot.utils.compat import dataclass
from nanobot.utils.helpers import (
    build_assistant_message,
    maybe_persist_tool_result,
    split_thinking_messages,
    truncate_text,
)
from nanobot.utils.media_decode import strip_image_blocks
from nanobot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
)

# Import from split modules
from .runner_constants import (
    _BACKFILL_CONTENT,
    _DEFAULT_ERROR_MESSAGE,
    _MAX_EMPTY_RETRIES,
    _MAX_INJECTION_CYCLES,
    _MAX_INJECTIONS_PER_TURN,
    _MAX_LENGTH_RECOVERIES,
    _MAX_MODEL_ERROR_RETRIES,
    _PERSISTED_MODEL_ERROR_PLACEHOLDER,
    _SNIP_SAFETY_BUFFER,
)
from .runner_retry import (
    BackoffConfig,
    BackoffStrategy,
    RetryContext,
    RetryState,
)

# Re-export for backward compatibility
__all__ = [
    "AgentRunSpec", "AgentRunResult", "AgentRunner",
    "_BACKFILL_CONTENT",
    "_MAX_EMPTY_RETRIES", "_MAX_INJECTION_CYCLES",
    "_MAX_INJECTIONS_PER_TURN", "_MAX_LENGTH_RECOVERIES",
    "_PERSISTED_MODEL_ERROR_PLACEHOLDER", "_SNIP_SAFETY_BUFFER",
    # Retry & checkpoint exports
    "BackoffConfig", "BackoffStrategy", "RetryContext", "RetryState",
]
from .runner_context import (
    backfill_missing_tool_results,
    drop_orphan_tool_results,
    strip_bypassed_tool_messages,
)
from .runner_execution import execute_tools
from .runner_injection import append_injected_messages, drain_injections
from .runner_llm import (
    accumulate_usage,
    merge_usage,
    request_finalization_retry,
    request_model,
    usage_dict,
)

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
    history_token_limit: int | None = None
    context_block_limit: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None
    # Retry & checkpoint configuration
    retry_context: Any | None = None  # RetryContext for retry tracking
    max_llm_retries: int = 3  # max retries for LLM errors
    max_overflow_retries: int = 3  # max retries for context window overflow
    backoff_config: Any | None = None  # BackoffConfig for retry delays
    # AssessMe: called when retry/error thresholds are crossed
    # Signature: async (messages: list[dict]) -> bool (True if injected)
    assess_me_callback: Any | None = None
    previous_summary: str | None = None


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
    # Retry tracking
    retry_count: int = 0  # total number of retries performed
    retry_summary: dict[str, Any] = field(default_factory=dict)  # detailed retry stats
    # Number of messages in result.messages that correspond to the initial
    # pre-loop input (system + history + user).  After reactive compression
    # this may differ from len(spec.initial_messages).  Used by callers of
    # _append_turn_to_session to correctly identify new-turn messages.
    initial_message_count: int = 0
    # Non-None when overflow compression occurred during this run
    overflow_summary: str | None = None


@dataclass(slots=True)
class _ToolLoopState:
    """State tracking for tool-call loop recovery (parameter validation errors)."""
    tool_name: str = ""
    error_sig: str = ""
    count: int = 0
    level: int = 0  # 0=normal, 1=assess_done, 2=compress_done, 3=max
    checked_iteration: int = -1


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self, provider: LLMProvider, db=None):
        self.provider = provider
        self._db = db

    async def _drain_injections(self, spec: AgentRunSpec) -> list[dict[str, Any]]:
        """Drain pending injections. Returns normalized user messages."""
        return await drain_injections(spec)

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list,
        external_lookup_counts: dict[str, int],
        messages: list[dict[str, Any]] | None = None,
        injection_cycles: int = 0,
        iteration: int = 0,
    ):
        """Backward compat wrapper — delegate to module function."""
        return await execute_tools(
            self, spec, tool_calls, external_lookup_counts,
            messages or [], injection_cycles, iteration,
        )

    async def _drain_injections_and_should_continue(
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
        initial_msg_count = len(messages)
        final_content: str | None = None
        tools_used: list[str] = []
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        error: str | None = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []
        external_lookup_counts: dict[str, int] = {}
        empty_content_retries = 0
        length_recovery_count = 0
        model_error_retries = 0
        consecutive_timeout_count = 0
        had_injections = False
        injection_cycles = 0
        total_retry_count = 0
        _tool_loop_state = _ToolLoopState()

        _current_messages_for_subagent.set(messages)

        # Track overflow summary from latest compression event
        _overflow_summary: str | None = None

        # Initialize retry context from spec
        retry_ctx = spec.retry_context
        backoff_cfg = spec.backoff_config

        if retry_ctx is None:
            retry_ctx = RetryContext()

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
                if isinstance(result, dict):
                    content = json.dumps(result, ensure_ascii=True)
                elif not isinstance(result, str):
                    content = str(result)
                else:
                    content = result
            if isinstance(content, str) and len(content) > spec.max_tool_result_chars:
                return truncate_text(content, spec.max_tool_result_chars)
            return content

        for iteration in range(spec.max_iterations):
            logger.debug(
                "Runner iteration {} t={:.1f}s model={} task={}",
                iteration, time.monotonic(), spec.model, spec.session_key or "?",
            )
            try:
                messages_for_model = strip_bypassed_tool_messages(messages)
                messages_for_model = drop_orphan_tool_results(messages_for_model)
                messages_for_model = backfill_missing_tool_results(messages_for_model)
                messages_for_model = split_thinking_messages(messages_for_model)
            except Exception as exc:
                logger.warning(
                    "Context governance failed on turn {} for {}: {}; applying minimal repair",
                    iteration, spec.session_key or "default", exc,
                )
                try:
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model)
                except Exception:
                    logger.exception(
                        "Context governance minimal repair failed on turn {} for {}; "
                        "falling back to raw messages",
                        iteration, spec.session_key or "default",
                    )
                    messages_for_model = messages

            context = AgentHookContext(iteration=iteration, messages=messages, workspace=spec.workspace)
            await hook.before_iteration(context)
            messages_for_model = hook.before_llm_call(context, messages_for_model)
            response, compress_event = await request_model(spec, messages_for_model, hook, context)
            # If MessagePipe compressed due to overflow, sync the compressed
            # messages back so the next iteration doesn't re-grow from old history.
            # Note: compress_event reflects the post-hook messages_for_model state.
            # If a custom hook's before_llm_call injected transient content,
            # it will be persisted into messages. Hook implementers should
            # avoid adding one-shot-only instructions via before_llm_call;
            # use before_iteration for persistent prep instead.
            if compress_event is not None:
                if compress_event.compressed_messages is not None:
                    messages[:] = compress_event.compressed_messages
                    initial_msg_count = len(messages)
                if compress_event.replaced_raw and self._db is not None:
                    try:
                        self._db.append_history(
                            content=json.dumps(compress_event.replaced_raw, ensure_ascii=True),
                            summary=compress_event.summary or "",
                        )
                    except Exception:
                        logger.exception("Failed to persist overflow-compressed messages to history")
                if compress_event.summary:
                    _overflow_summary = compress_event.summary
            # Images are only useful once — strip base64 payloads so
            # subsequent turns don't re-send megabytes of image data.
            # The model can re-read with read_file_tool if needed.
            if response.finish_reason != "error":
                strip_image_blocks(messages)
            raw_usage = usage_dict(response.usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            accumulate_usage(usage, raw_usage)

            if response.should_execute_tools:
                tool_calls = list(response.tool_calls)
                context.tool_calls = list(tool_calls)
                if response.content:
                    await hook.on_stream(context, response.content)
                await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in tool_calls],
                    reasoning_content=response.reasoning_content,
                    reasoning_details=response.reasoning_details,
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
                tool_calls = hook.filter_tool_calls(context, tool_calls)
                if not tool_calls:
                    messages.append(build_assistant_message(response.content or ""))
                    continue

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
                    executed_count = min(executed_count, len(tool_calls))

                    # 1. Append tool results for executed tools
                    for i in range(executed_count):
                        tc = tool_calls[i]
                        res = results[i]
                        content = _normalize(spec, tc.id, tc.name, res)
                        ts = res.timestamp.isoformat() if hasattr(res, "timestamp") and res.timestamp else datetime.now(timezone.utc).isoformat()
                        tool_message = {
                            "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                            "content": content, "timestamp": ts,
                        }
                        messages.append(tool_message)
                        completed_tool_results.append(tool_message)

                    # 2. Build closing assistant — facts only, no intent guidance
                    pending_names = [tc.name for tc in tool_calls[executed_count:]]
                    parts = []
                    is_tool_failure = isinstance(fatal_error, RuntimeError) and executed_count > 0
                    if is_tool_failure:
                        failed_name = tool_calls[executed_count - 1].name
                        success_names = [tc.name for tc in tool_calls[:executed_count - 1]]
                        if success_names:
                            parts.append("、".join(success_names) + " 已完成")
                        parts.append(f"{failed_name} 失败")
                    elif executed_count > 0:
                        parts.append("、".join(tc.name for tc in tool_calls[:executed_count]) + " 已完成")
                    if pending_names:
                        parts.append("、".join(pending_names) + " 已推迟")
                    if saved_injections:
                        parts.append("你插入了新消息，我会优先响应并做出合适安排。")
                    closing_text = "。".join(parts)

                    # Preserve original assistant text content (Scenario 2)
                    orig_content = assistant_message.get("content", "") or ""
                    if orig_content.strip():
                        closing_text = orig_content + "\n\n" + closing_text

                    # 3. Strip unexecuted tool_calls from original assistant (1b/1c)
                    #    Create a NEW dict to avoid mutating session.messages references
                    if executed_count < len(tool_calls):
                        if executed_count == 0:
                            messages.pop()  # Remove original assistant entirely
                        else:
                            new_msg = dict(assistant_message)
                            new_msg["tool_calls"] = [
                                tc.to_openai_tool_call() for tc in tool_calls[:executed_count]
                            ]
                            # Assistant was appended before tool results;
                            # find its position relative to the end
                            asst_idx = len(messages) - 1 - executed_count
                            messages[asst_idx] = new_msg
                            assistant_message = new_msg

                    # 4. Append closing assistant → legal ... → assistant → user
                    messages.append(build_assistant_message(closing_text))

                    # 5. Append saved user injections after the closing assistant
                    if saved_injections:
                        messages.extend(saved_injections)
                        logger.info(
                            "Injected {} saved follow-up message(s) after interruption ({}/{})",
                            len(saved_injections), injection_cycles, _MAX_INJECTION_CYCLES,
                        )

                    empty_content_retries = 0
                    length_recovery_count = 0
                    had_injections = True
                    await hook.after_iteration(context)
                    continue

                completed_tool_results = []
                for tool_call, result, ev in zip(tool_calls, results, new_events):
                    content = _normalize(spec, tool_call.id, tool_call.name, result)
                    ts = result.timestamp.isoformat() if hasattr(result, "timestamp") and result.timestamp else datetime.now(timezone.utc).isoformat()
                    tool_message = {
                        "role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name,
                        "content": content, "timestamp": ts,
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)

                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    should_continue, injection_cycles = await self._drain_injections_and_should_continue(
                        spec, messages, None, injection_cycles, phase="after tool error",
                    )
                    if should_continue:
                        had_injections = True
                        continue
                    # Tool error — feed back to LLM for self-correction
                    continue

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

                # Tool loop recovery: detect repeated param validation errors
                if tool_calls and not fatal_error:
                    recovery_action = await self._check_tool_loop(
                        _tool_loop_state, tool_calls, new_events, messages, iteration,
                    )
                    if recovery_action == "assess_me":
                        assess_text = await _run_assess_me(messages)
                        if assess_text:
                            messages.append(build_assessment_message(assess_text))
                            had_injections = True
                            logger.info(
                                "Tool loop recovery: injected assess_me "
                                "(iteration {}, tool={})",
                                iteration, _tool_loop_state.tool_name,
                            )
                    elif recovery_action == "compress":
                        tool_name = _tool_loop_state.tool_name
                        logger.info(
                            "Tool loop recovery: injecting info-reminder "
                            "(iteration {}, tool={})",
                            iteration, tool_name,
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[Tool call errors detected: consecutive failures on '{tool_name}'.\n"
                                "This is often caused by missing information — "
                                "wrong arguments, file paths, or context gaps.\n"
                                "Use input tools to gather what you need "
                                "before retrying.]"
                            ),
                        })
                    elif recovery_action == "force_stop":
                        final_content = _force_final_response(
                            messages,
                            "Tool calls failed repeatedly with parameter validation errors. "
                            "Please check arguments and retry.",
                        )
                        stop_reason = "tool_loop_breaker"
                        context.final_content = final_content
                        context.stop_reason = stop_reason
                        context.error = final_content
                        await hook.after_iteration(context)
                        break
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason, spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)
            if response.finish_reason != "error" and is_blank_text(clean):
                empty_content_retries += 1
                total_retry_count += 1
                retry_ctx.empty_response_state.record_attempt(f"empty response on attempt {empty_content_retries}")
                if empty_content_retries < _MAX_EMPTY_RETRIES:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        iteration, spec.session_key or "default",
                        empty_content_retries, _MAX_EMPTY_RETRIES,
                    )
                    # Apply backoff before retry
                    await retry_ctx.wait_with_backoff(
                        "empty_response",
                        retry_callback=spec.retry_wait_callback,
                        config=backoff_cfg,
                    )
                    await hook.on_stream_end(context, resuming=False)
                    # Run assess_me between retries — the model may need to
                    # re-orient before its next attempt at the same context
                    if spec.assess_me_callback is not None:
                        await spec.assess_me_callback(messages)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration, spec.session_key or "default", empty_content_retries,
                )
                await hook.on_stream_end(context, resuming=False)
                assess_injected = False
                if spec.assess_me_callback is not None:
                    assess_injected = await spec.assess_me_callback(messages) or False
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model)
                response = await request_finalization_retry(spec, messages_for_model, has_assessment=assess_injected)
                retry_usage = usage_dict(response.usage)
                accumulate_usage(usage, retry_usage)
                raw_usage = merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = dict(raw_usage)
                context.tool_calls = list(response.tool_calls)
                clean = hook.finalize_content(context, response.content)
                retry_ctx.empty_response_state.record_success()

            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                total_retry_count += 1
                retry_ctx.length_recovery_state.record_attempt(f"length recovery attempt {length_recovery_count}")
                if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration, spec.session_key or "default",
                        length_recovery_count, _MAX_LENGTH_RECOVERIES,
                    )
                    # Run assess_me before length recovery — the truncated
                    # output may leave the model mid-thought; assess_me helps
                    # it re-establish context before continuing
                    if spec.assess_me_callback is not None:
                        await spec.assess_me_callback(messages)
                    await hook.on_stream_end(context, resuming=True)
                    messages.append(build_assistant_message(
                        clean,
                        reasoning_content=response.reasoning_content,
                        reasoning_details=response.reasoning_details,
                        thinking_blocks=response.thinking_blocks,
                    ))
                    messages.append(build_length_recovery_message())
                    await hook.after_iteration(context)
                    retry_ctx.length_recovery_state.record_success()
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    reasoning_details=response.reasoning_details,
                    thinking_blocks=response.thinking_blocks,
                )

            should_continue, injection_cycles = await self._drain_injections_and_should_continue(
                spec, messages, assistant_message, injection_cycles,
                phase="after final response", iteration=iteration,
            )
            if should_continue:
                had_injections = True

            await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                if response.error_kind == "timeout":
                    consecutive_timeout_count += 1
                else:
                    consecutive_timeout_count = 0
                final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._drain_injections_and_should_continue(
                    spec, messages, None, injection_cycles, phase="after LLM error",
                )
                if should_continue:
                    had_injections = True
                    continue
                if model_error_retries < _MAX_MODEL_ERROR_RETRIES:
                    # Compress context on repeated timeout: keep last 10 turns,
                    # summarize older turns so the retry works with less context.
                    if consecutive_timeout_count >= 3:
                        from nanobot.agent.compress import compress_turns

                        turns = Session._split_turns_by_assistant(messages)
                        if len(turns) > 10:
                            s_turns = turns[:-10]
                            boundary = sum(len(t) for t in s_turns)
                            summary, pair = await compress_turns(
                                [m for t in s_turns for m in t],
                                [m for t in turns[-10:] for m in t],
                                timestamp=datetime.now(timezone.utc).isoformat(),
                            )
                            if pair:
                                for m in messages[:boundary]:
                                    m["status"] = "excluded"
                                messages[boundary:boundary] = pair
                            logger.warning(
                                "Summarized {} old turns {} for {} after consecutive timeouts",
                                len(s_turns),
                                f"({len(str(summary))} chars)" if summary else "(summary failed)",
                                spec.session_key or "default",
                            )
                        consecutive_timeout_count = 0
                    model_error_retries += 1
                    total_retry_count += 1
                    retry_ctx.llm_request_state.record_attempt(f"model error retry {model_error_retries}")
                    # Apply backoff for model errors
                    await retry_ctx.wait_with_backoff(
                        "llm_request",
                        retry_callback=spec.retry_wait_callback,
                        config=backoff_cfg,
                    )
                    messages.append(build_assistant_message(
                        "[My previous response was blocked by content safety. I'll reformulate and try again.]"
                    ))
                    # Run assess_me before reformulation — the model may not
                    # realize its response was blocked; assess_me reframes its
                    # intention for the retry
                    if spec.assess_me_callback is not None:
                        await spec.assess_me_callback(messages)
                    empty_content_retries = 0
                    retry_ctx.llm_request_state.record_success()
                    continue
                if spec.assess_me_callback is not None:
                    assess_injected = await spec.assess_me_callback(messages) or False
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model)
                    response = await request_finalization_retry(spec, messages_for_model, has_assessment=assess_injected)
                    retry_usage = usage_dict(response.usage)
                    accumulate_usage(usage, retry_usage)
                    raw_usage = merge_usage(raw_usage, retry_usage)
                    context.response = response
                    context.usage = dict(raw_usage)
                    context.tool_calls = list(response.tool_calls)
                    clean = hook.finalize_content(context, response.content)
                    if response.finish_reason != "error":
                        stop_reason = "completed"
                        error = None
                    else:
                        break
                else:
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
                should_continue, injection_cycles = await self._drain_injections_and_should_continue(
                    spec, messages, None, injection_cycles, phase="after empty response",
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            messages.append(assistant_message or build_assistant_message(
                clean,
                reasoning_content=response.reasoning_content,
                reasoning_details=response.reasoning_details,
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
            drained_after_max_iterations, injection_cycles = await self._drain_injections_and_should_continue(
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
            retry_count=total_retry_count,
            retry_summary=retry_ctx.summary(),
            initial_message_count=initial_msg_count,
            overflow_summary=_overflow_summary,
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
            logger.exception("Failed to log tool call to DB")

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

    async def _check_tool_loop(
        self,
        state: _ToolLoopState,
        tool_calls: list,
        new_events: list,
        messages: list[dict],
        iteration: int,
    ) -> str | None:
        """Check for repeated parameter validation errors.

        Returns None (no action), "assess_me", "compress", or "force_stop".
        """
        if iteration == state.checked_iteration:
            return None
        state.checked_iteration = iteration

        # Collect parameter validation errors only
        param_errors: list[tuple[str, str]] = []
        for tc, ev in zip(tool_calls, new_events):
            detail = ev.get("detail", "")
            if ev.get("status") == "error" and "Invalid parameters" in detail:
                param_errors.append((tc.name, detail))

        if not param_errors:
            state.tool_name = ""
            state.error_sig = ""
            state.count = 0
            state.level = 0
            return None

        tool_name = param_errors[0][0]
        error_sig = param_errors[0][1][:80]

        if tool_name == state.tool_name and error_sig == state.error_sig:
            state.count += 1
        else:
            state.tool_name = tool_name
            state.error_sig = error_sig
            state.count = 1
            state.level = 0
            return None

        if state.count < 3:
            return None

        # Threshold crossed — escalate
        state.count = 0

        if state.level == 0:
            state.level = 1
            return "assess_me"
        elif state.level == 1:
            state.level = 2
            return "compress"
        elif state.level == 2:
            state.level = 3
            return "force_stop"
        return None

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    # Backward compatibility — delegate to module functions
    _drop_orphan_tool_results = staticmethod(drop_orphan_tool_results)
    _backfill_missing_tool_results = staticmethod(backfill_missing_tool_results)



def _force_final_response(messages: list[dict], text: str) -> str:
    """Strip the last broken tool-call turn and append a final text response."""
    if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
        messages.pop()
    while messages and messages[-1].get("role") == "tool":
        messages.pop()
    messages.append(build_assistant_message(text))
    return text
