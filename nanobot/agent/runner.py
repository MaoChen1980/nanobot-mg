"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.agent.assess_me import (
    assess_message_content,
    build_assessment_message,
    format_conversation,
    is_assessment_message,
)
from nanobot.agent.assess_me import assess_me as _run_assess_me
from nanobot.agent.context_vars import _current_messages_for_subagent
from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session
from nanobot.utils.compat import dataclass
from nanobot.utils.helpers import (
    build_assistant_message,
    format_timestamp_cst,
    maybe_persist_tool_result,
    split_thinking_messages,
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
    deduplicate_tool_call_ids,
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
    compress_trigger_tokens: int | None = None
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
    # Signature: async (messages: list[dict]) -> AssessResult
    assess_me_callback: Any | None = None
    assess_interval: int = 10  # periodic assess trigger: (response_count - last) >= interval
    previous_summary: str | None = None
    instructions: str | None = None  # injected at index 1 each iteration; can be a callable () -> str | None for per-iteration refresh
    prompts_dir: Path | None = None  # save .pt snapshots in runner loop
    pt_save_interval: int = 30  # .pt snapshot: every N LLM responses
    # Subagent lifecycle: callback returns active subagent count for the current session
    subagent_running_callback: Callable[[], int] | None = None
    subagent_wait_timeout: int = 600  # max seconds to wait for subagents before forcing break
    # Keyword-based automatic memory search: called when LLM outputs <!-- kw: ... --> tag.
    # Receives the keyword string, returns list of memory search result dicts.
    keyword_search_callback: Callable[[str], list[dict]] | None = None


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
    # Total LLM API calls made during this run (includes retries)
    total_llm_requests: int = 0


@dataclass(slots=True)
class AssessResult:
    """Result of an assess_me callback execution.

    ``__bool__`` returns ``self.injected`` so existing ``if injected:``
    checks continue working without changes.

    **Message mutation contract:**
    The assess callback evaluations the conversation and returns pre-built
    injection messages.  The runner (``_run_assess_callback``) applies them
    to ``messages`` — the callback itself **must not** mutate the message
    list.
    """
    injected: bool = False
    needs_revision: bool = False
    # Pre-built assessment / DRC messages to inject into the conversation.
    # Applied by _run_assess_callback — the callback returns data, the runner mutates.
    injection_messages: list[dict] | None = None

    def __bool__(self) -> bool:
        return self.injected


@dataclass(slots=True)
class _ToolLoopState:
    """State tracking for tool-call loop recovery (any tool errors + empty results)."""
    # Per-tool error tracking
    tool_name: str = ""
    error_sig: str = ""
    count: int = 0
    level: int = 0  # 0=normal, 1=assess_done, 2=compress_done, 3=max
    checked_iteration: int = -1
    # Running error tally (any tool, any error)
    consecutive_errors: int = 0
    # Empty result tracking
    empty_tool: str = ""
    empty_count: int = 0


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self, provider: LLMProvider, db=None):
        self.provider = provider
        self._db = db
        self._current_spec: AgentRunSpec | None = None
        self._assess_responses = 0  # periodic assess_me counter (local to this run, resets on fire)
        self._pt_responses = 0  # periodic .pt snapshot counter (local to this run)

    async def _maybe_compress_messages(
        self,
        spec: AgentRunSpec,
        messages: list[dict],
        initial_msg_count: int,
        overflow_summary: str | None,
    ) -> tuple[int, str | None]:
        """Proactively compress messages if total tokens exceed compress_trigger_tokens.

        Returns (updated_initial_msg_count, updated_overflow_summary).
        Never raises: all exceptions caught, original values returned on failure.
        """
        if spec.compress_trigger_tokens is None or spec.history_token_limit is None:
            return initial_msg_count, overflow_summary
        if len(messages) <= 2:
            return initial_msg_count, overflow_summary

        from nanobot.agent.compressor import Compressor
        from nanobot.utils.helpers import estimate_message_tokens

        try:
            total_tokens = sum(estimate_message_tokens(m) for m in messages)
            if total_tokens <= spec.compress_trigger_tokens:
                return initial_msg_count, overflow_summary

            system_prompt = messages[0]
            _has_instr = (
                len(messages) > 1
                and messages[1].get("role") == "user"
                and isinstance(messages[1].get("content"), str)
                and messages[1]["content"].startswith("## Instructions")
            )
            rest_start = 2 if _has_instr else 1
            rest = messages[rest_start:]

            turns = Compressor.split_turns(rest)
            if len(turns) <= 1:
                return initial_msg_count, overflow_summary

            to_compress, to_keep = Compressor.split_by_budget(
                turns, budget=spec.history_token_limit, min_keep=1,
            )
            if not to_compress:
                return initial_msg_count, overflow_summary

            prev_summary = overflow_summary or spec.previous_summary
            event = await Compressor.compress(
                to_compress, to_keep,
                previous_summary=prev_summary,
            )
            if not event.synthetic_pair:
                return initial_msg_count, overflow_summary

            n_compressed = sum(len(t) for t in to_compress)
            compressed_raw = messages[rest_start:rest_start + n_compressed]

            if self._db is not None and compressed_raw:
                try:
                    self._db.append_history(
                        content=json.dumps(compressed_raw, ensure_ascii=True),
                        summary=event.summary or "",
                    )
                except Exception:
                    logger.exception("Failed to persist proactively-compressed messages to history")

            result = [system_prompt]
            if _has_instr:
                result.append(messages[1])  # preserve ## Instructions at index 1
            result.extend(event.synthetic_pair)
            for turn in to_keep:
                result.extend(turn)
            messages[:] = result

            new_overflow_summary = event.summary or overflow_summary
            logger.info(
                "Proactive compression: compressed {} messages into {} synthetic, "
                "kept {} turns (total now {} msgs, summary={})",
                n_compressed, len(event.synthetic_pair), len(to_keep),
                len(messages), bool(event.summary),
            )
            return len(messages), new_overflow_summary

        except Exception:
            logger.exception("Proactive compression failed (session={})", spec.session_key or "default")
            return initial_msg_count, overflow_summary

    async def _run_assess_callback(self, spec: AgentRunSpec, messages: list[dict], timeout: float = 100, retry_count: int = 2) -> AssessResult:
        """Run assess_me_callback with timeout protection and retries.

        The callback returns evaluation data; this method strips stale
        assessment messages from *messages* before injecting new ones,
        keeping at most one assessment in the conversation at any time.

        Retries on TimeoutError up to *retry_count* times; other exceptions
        propagate immediately.
        """
        if spec.assess_me_callback is None:
            return AssessResult()
        for attempt in range(1 + retry_count):
            try:
                logger.debug(
                    "assess_me_callback start (attempt={}/{}, session_key={})",
                    attempt, 1 + retry_count, spec.session_key,
                )
                result = await asyncio.wait_for(spec.assess_me_callback(messages), timeout=timeout)
                if result.injection_messages:
                    messages[:] = [m for m in messages if not is_assessment_message(m)]
                    messages.extend(result.injection_messages)
                    logger.info(
                        "assess_me_callback injected {} message(s) (session_key={})",
                        len(result.injection_messages), spec.session_key,
                    )
                return result
            except asyncio.TimeoutError:
                logger.warning(
                    "assess_me_callback attempt {}/{} timed out after {}s (session_key={})",
                    attempt, 1 + retry_count, timeout, spec.session_key,
                )
        logger.warning(
            "assess_me_callback exhausted all {} attempts (session_key={})",
            1 + retry_count, spec.session_key,
        )
        return AssessResult()

    async def _assess_sent_messages(
        self,
        tool_calls: list,
        new_events: list[dict],
        messages: list[dict],
    ) -> None:
        """Post-send quality check: assess sent message content and inject user-role feedback.

        Runs after the message tool has already sent content. If the assessment finds
        quality issues, a user-role message is injected into the conversation so the
        LLM can improve its output on the next turn.
        """
        for i, tc in enumerate(tool_calls):
            if tc.name != "message":
                continue
            ev = new_events[i] if i < len(new_events) else {}
            if ev.get("status") != "ok":
                continue
            content = tc.arguments.get("content", "") or ""
            if not content:
                continue
            try:
                result = await assess_message_content(content, context=format_conversation(messages))
                if result is None or result.get("status") == "ok":
                    continue
                issues = result.get("issues", [])
                summary = result.get("summary", "消息内容质量存在问题")
                inject_text = (
                    f"消息质量评估 — 已发出的内容存在以下问题：\n"
                    + "\n".join(f"- {i}" for i in issues)
                    + f"\n\n{summary}"
                )
                messages[:] = [m for m in messages if not is_assessment_message(m)]
                messages.append(build_assessment_message(inject_text))
                logger.info("Injected post-send quality assessment for message tool call")
            except Exception:
                logger.exception("Post-send message quality check failed")

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

    async def _wait_subagents(
        self,
        spec: AgentRunSpec,
        injection_cycles: int = 0,
    ) -> list[dict[str, Any]]:
        """Wait for subagents to complete. No LLM calls during wait.

        Drains injection queue — when subagent results arrive they get
        picked up naturally. Returns injected messages or empty list.
        """
        if not spec.subagent_running_callback:
            return []
        deadline = time.monotonic() + spec.subagent_wait_timeout
        poll = 2.0
        last_progress = 0.0
        while time.monotonic() < deadline:
            try:
                active = spec.subagent_running_callback()
            except Exception:
                logger.exception("subagent_running_callback failed")
                break

            if injection_cycles >= _MAX_INJECTION_CYCLES:
                logger.warning(
                    "Injection cycle limit reached ({}) while waiting for subagents, "
                    "breaking wait (session={})",
                    _MAX_INJECTION_CYCLES, spec.session_key or "?",
                )
                break

            if active == 0:
                # Yield event loop so AgentLoop.run() can drain any just-completed
                # subagent bus messages into the injection queue.
                await asyncio.sleep(0)
                residual = await drain_injections(spec)
                if residual:
                    logger.info("Drained {} residual subagent result(s)", len(residual))
                    return residual
                # Subagent task completed but results may not have propagated
                # through the async delivery chain yet. Retry before giving up.
                for _ in range(3):
                    await asyncio.sleep(0.2)
                    residual = await drain_injections(spec)
                    if residual:
                        logger.info(
                            "Drained {} residual subagent result(s) after retry",
                            len(residual),
                        )
                        return residual
                return []

            # Drain injection queue — subagent results arrive here
            injections = await drain_injections(spec)
            if injections:
                logger.info("Got {} subagent result(s) after wait", len(injections))
                return injections

            # Progress notification to user (every 10s)
            now = time.monotonic()
            if now - last_progress >= 10.0:
                last_progress = now
                msg = f"Waiting for {active} subagent(s) to complete..."
                if spec.progress_callback:
                    try:
                        await spec.progress_callback(
                            msg,
                            tool_hint=False,
                            tool_events=None,
                        )
                    except Exception:
                        logger.exception("progress_callback failed")
                logger.info("Waiting for {} subagent(s) to complete", active)

            await asyncio.sleep(poll)
            poll = min(poll * 1.5, 10.0)

        try:
            remaining = spec.subagent_running_callback()
        except Exception:
            remaining = -1
        logger.warning(
            "Timed out waiting for subagents ({} active, timeout={}s, session={})",
            remaining, spec.subagent_wait_timeout, spec.session_key or "?",
        )
        return []

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        self._current_spec = spec
        # Reset per-run counters — AgentRunner is long-lived, run() can be called multiple times
        self._assess_responses = 0
        self._pt_responses = 0
        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        run_context = AgentRunHookContext(messages=list(messages))
        await hook.before_run(run_context)

        try:
            return await self._run_core(spec, hook, messages)
        except BaseException as _hook_exc:
            run_context.messages = list(messages)
            run_context.stop_reason = "error"
            run_context.error = f"Error: {type(_hook_exc).__name__}: {_hook_exc}"
            run_context.exception = _hook_exc
            await hook.on_error(run_context)
            raise
        finally:
            run_context.messages = list(messages)
            await hook.after_run(run_context)
            await hook.on_finally(run_context)

    async def _run_core(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict],
    ) -> AgentRunResult:
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
        _llm_request_count = 0
        _end_assess_ran = False
        _tool_loop_state = _ToolLoopState()
        _pending_memory: str | None = None  # memory text to inject into next iteration's instructions

        _current_messages_for_subagent.set(messages)
        _run_t0 = time.monotonic()

        # Track overflow summary from latest compression event
        _overflow_summary: str | None = None

        # Initialize retry context from spec
        retry_ctx = spec.retry_context
        backoff_cfg = spec.backoff_config

        if retry_ctx is None:
            retry_ctx = RetryContext()

        def _normalize(spec, tc_id, name, result, *, duration_ms=0, status="ok"):
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

            # Detect persistence → extract preview and result_file
            result_file = None
            truncated = False
            actual_result = content
            if isinstance(content, str) and content.startswith("[tool output persisted]"):
                truncated = True
                for line in content.split("\n"):
                    if line.startswith("Full output saved to: "):
                        result_file = line[len("Full output saved to: "):]
                        break
                preview_start = content.find("Preview:\n")
                if preview_start >= 0:
                    actual_result = content[preview_start + len("Preview:\n"):]
                    # Strip trailing hint if present
                    tail = "\n...\n(Read the saved file if you need the full output.)"
                    if actual_result.endswith(tail):
                        actual_result = actual_result[:-len(tail)]

            # Last-resort truncation for non-persisted results
            if not truncated and isinstance(actual_result, str) and len(actual_result) > spec.max_tool_result_chars:
                actual_result = truncate_text(actual_result, spec.max_tool_result_chars)

            result_length = len(actual_result) if isinstance(actual_result, str) else 0
            is_error = status == "error" or (isinstance(actual_result, str) and actual_result.startswith("Error"))

            wrapper = {
                "status": "fail" if is_error else "ok",
                "tool": name,
                "duration_s": round(duration_ms / 1000, 3),
                "result": actual_result,
                "result_length": result_length,
                "result_file": result_file,
                "truncated": truncated,
                "error": actual_result if is_error else None,
            }

            return json.dumps(wrapper, ensure_ascii=False)

        for iteration in range(spec.max_iterations):
            logger.debug(
                "Runner iteration {} t={:.1f}s model={} task={}",
                iteration, time.monotonic(), spec.model, spec.session_key or "?",
            )
            # --- PROACTIVE COMPRESSION ---
            initial_msg_count, _overflow_summary = await self._maybe_compress_messages(
                spec, messages, initial_msg_count, _overflow_summary,
            )
            # --- END PROACTIVE COMPRESSION ---
            try:
                messages_for_model = strip_bypassed_tool_messages(messages)
                messages_for_model = deduplicate_tool_call_ids(messages_for_model)
                messages_for_model = drop_orphan_tool_results(messages_for_model)
                messages_for_model = backfill_missing_tool_results(messages_for_model)
                messages_for_model = split_thinking_messages(messages_for_model, spec.model)
            except Exception as exc:
                logger.warning(
                    "Context governance failed on turn {} for {}: {}; applying minimal repair",
                    iteration, spec.session_key or "default", exc,
                )
                try:
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model, spec.model)
                except Exception:
                    logger.exception(
                        "Context governance minimal repair failed on turn {} for {}; "
                        "falling back to raw messages",
                        iteration, spec.session_key or "default",
                    )
                    messages_for_model = messages

            context = AgentHookContext(iteration=iteration, messages=messages, workspace=spec.workspace)
            logger.info("RUN_DBG: before_iteration (iter={})", iteration)
            await hook.before_iteration(context)
            logger.info("RUN_DBG: before_llm_call (iter={})", iteration)
            messages_for_model = hook.before_llm_call(context, messages_for_model)

            _ensure_instructions(messages_for_model, spec)

            # Inject pending memory into instructions block
            if _pending_memory and len(messages_for_model) > 1:
                _instr_msg = messages_for_model[1]
                if (_instr_msg.get("role") == "user"
                        and isinstance(_instr_msg.get("content"), str)
                        and _instr_msg["content"].startswith("## Instructions")):
                    _instr_msg["content"] += "\n\n" + _pending_memory
                    _pending_memory = None

            logger.info("RUN_DBG: request_model start (iter={}, msgs={})", iteration, len(messages_for_model))
            response, compress_event = await request_model(spec, messages_for_model, hook, context)
            _llm_request_count += 1
            logger.info("RUN_DBG: request_model done (iter={}, finish={}, tools={})",
                        iteration, response.finish_reason, len(response.tool_calls))

            # --- Extract memory keywords from LLM response ---
            _kw_query: str | None = None
            if response.content and response.should_execute_tools:
                _kw_match = re.search(r'<!--\s*kw:\s*(.+?)\s*-->', response.content, flags=re.DOTALL)
                if _kw_match:
                    _kw_query = _kw_match.group(1).strip()
                    if not _kw_query:
                        _kw_query = None
                    else:
                        logger.debug("Extracted memory keywords: {}", _kw_query)
                    # Strip ALL keyword tags from response content — internal mechanism, not for user
                    response.content = re.sub(r'<!--\s*kw:\s*.*?\s*-->', '', response.content, flags=re.DOTALL)
            # ---------------------------------------------------

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
            # Periodic self-assessment — fire at milestones (every assess_interval responses)
            # within this run, not just at user-message boundaries.
            # Uses threshold (>=) instead of exact multiple (%) so batch jumps don't skip.
            # .pt snapshot — every N LLM responses (independent counter)
            if spec.prompts_dir is not None and spec.session_key:
                self._pt_responses += 1
                if self._pt_responses >= spec.pt_save_interval:
                    self._pt_responses = 0
                    try:
                        from nanobot.agent.memory_extractor import MemoryExtractor
                        path = MemoryExtractor.save_prompt_snapshot(messages_for_model, spec.prompts_dir, spec.session_key)
                        logger.info("Saved .pt snapshot: {} ({} msgs, session={})", path.name, len(messages_for_model), spec.session_key)
                    except Exception:
                        logger.exception("Failed to save .pt snapshot (session={})", spec.session_key)
            # Periodic self-assessment — fire every assess_interval responses, reset on fire
            if spec.assess_me_callback is not None:
                self._assess_responses += 1
                if self._assess_responses >= spec.assess_interval:
                    self._assess_responses = 0
                    await self._run_assess_callback(spec, messages)
            _now = time.monotonic()
            logger.info("RUN_Timing: post_llm={:.0f}ms", (_now - _run_t0) * 1000)
            raw_usage = usage_dict(response.usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            accumulate_usage(usage, raw_usage)

            if response.should_execute_tools:
                tool_calls = list(response.tool_calls)
                context.tool_calls = list(tool_calls)
                if response.content and not hook._had_content:
                    await hook.on_stream(context, response.content)
                await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in tool_calls],
                    reasoning_content=response.reasoning_content,
                    reasoning_details=response.reasoning_details,
                    thinking_blocks=response.thinking_blocks,
                    model=spec.model,
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
                    messages.pop()  # No tools to execute, remove assistant with stale tool_calls
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
                        ev = new_events[i] if i < len(new_events) else {}
                        content = _normalize(spec, tc.id, tc.name, res,
                                              duration_ms=ev.get("duration_ms", 0),
                                              status=ev.get("status", "ok"))
                        ts = format_timestamp_cst(res.timestamp) if hasattr(res, "timestamp") and res.timestamp else format_timestamp_cst()
                        tool_message = {
                            "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                            "content": content, "timestamp": ts,
                        }
                        messages.append(tool_message)
                        completed_tool_results.append(tool_message)

                    # 2. Strip unexecuted tool_calls from original assistant
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

                    # 3. Merge saved user messages into the last user message
                    if saved_injections:
                        for i in range(len(messages) - 1, -1, -1):
                            if messages[i].get("role") == "user":
                                new_parts = [m.get("content", "") for m in saved_injections if m.get("content")]
                                if new_parts:
                                    head = messages[i]["content"]
                                    sep = "\n\n---\n[以下为新消息]\n" if head.strip() else ""
                                    messages[i]["content"] = head + sep + "\n".join(new_parts)
                                break
                        logger.info(
                            "Merged {} follow-up message(s) into last user message ({}/{})",
                            len(saved_injections), injection_cycles, _MAX_INJECTION_CYCLES,
                        )

                    empty_content_retries = 0
                    length_recovery_count = 0
                    had_injections = True
                    await hook.after_iteration(context)
                    continue

                completed_tool_results = []
                for tool_call, result, ev in zip(tool_calls, results, new_events):
                    content = _normalize(spec, tool_call.id, tool_call.name, result,
                                          duration_ms=ev.get("duration_ms", 0),
                                          status=ev.get("status", "ok"))
                    ts = format_timestamp_cst(result.timestamp) if hasattr(result, "timestamp") and result.timestamp else format_timestamp_cst()
                    tool_message = {
                        "role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name,
                        "content": content, "timestamp": ts,
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)

                # Post-send quality check: assess sent message content and inject
                # user-role feedback if issues found. Runs after the message tool has
                # already sent — the tool contract is preserved.
                try:
                    await self._assess_sent_messages(tool_calls, new_events, messages)
                except Exception:
                    logger.exception("Post-send message assessment failed")

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

                # Tool loop recovery: detect repeated tool errors
                if tool_calls and not fatal_error:
                    recovery_action = await self._check_tool_loop(
                        _tool_loop_state, tool_calls, new_events, messages, iteration,
                    )
                    if recovery_action == "assess_me":
                        assess_text = await _run_assess_me(messages)
                        if assess_text:
                            for i in range(len(messages) - 1, -1, -1):
                                if is_assessment_message(messages[i]):
                                    messages.pop(i)
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
                            "工具调用因参数校验错误反复失败，请检查参数后重试。",
                        )
                        stop_reason = "tool_loop_breaker"
                        context.final_content = final_content
                        context.stop_reason = stop_reason
                        context.error = final_content
                        break

                # --- Store memory from keyword tag for next iteration ---
                if _kw_query and spec.keyword_search_callback:
                    try:
                        results = await asyncio.to_thread(spec.keyword_search_callback, _kw_query)
                        if results:
                            _MAX_MEMORY_CHARS = 8000
                            mem_parts = ["", "### 自动检索的相关记忆（根据关键字自动检索）", ""]
                            remaining = len(results)
                            for r in results:
                                remaining -= 1
                                source = r.get("source", "unknown")
                                heading = r.get("heading", "")
                                text = r.get("text", "")
                                text_snippet = text[:1000] + "..." if len(text) > 1000 else text
                                mem_parts.append(f"> **{source}**")
                                if heading:
                                    mem_parts.append(f"> *{heading}*")
                                mem_parts.append(f"> {text_snippet}")
                                mem_parts.append("")
                                if sum(len(p) for p in mem_parts) >= _MAX_MEMORY_CHARS and remaining > 0:
                                    mem_parts.append(f"> *...还有 {remaining} 条相关记忆未显示*")
                                    break
                            _pending_memory = "\n".join(mem_parts)
                            logger.info("Stored memory from keywords ({} results, {} chars): {}", len(results), len(_pending_memory), _kw_query)
                        else:
                            logger.debug("No memory results for keywords: {}", _kw_query)
                    except Exception as exc:
                        logger.warning("Keyword memory search failed: {}", exc)
                # ------------------------------------------------

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
                        await self._run_assess_callback(spec, messages)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration, spec.session_key or "default", empty_content_retries,
                )
                await hook.on_stream_end(context, resuming=False)
                assess_injected = False
                if spec.assess_me_callback is not None:
                    assess_injected = await self._run_assess_callback(spec, messages)
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model, spec.model)
                    _ensure_instructions(messages_for_model, spec)
                response = await request_finalization_retry(spec, messages_for_model, has_assessment=assess_injected)
                _llm_request_count += 1
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
                        await self._run_assess_callback(spec, messages)
                    await hook.on_stream_end(context, resuming=True)
                    messages.append(build_assistant_message(
                        clean,
                        reasoning_content=response.reasoning_content,
                        reasoning_details=response.reasoning_details,
                        thinking_blocks=response.thinking_blocks,
                        model=spec.model,
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
                    model=spec.model,
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
            _now = time.monotonic()
            logger.info("RUN_Timing: post_stream_end={:.0f}ms", (_now - _run_t0) * 1000)

            if response.finish_reason == "error":
                if response.error_kind == "timeout":
                    consecutive_timeout_count += 1
                else:
                    consecutive_timeout_count = 0
                final_content = clean if clean and not is_blank_text(clean) else (spec.error_message or _DEFAULT_ERROR_MESSAGE)
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
                                timestamp=format_timestamp_cst(),
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
                    messages.append({
                        "role": "user",
                        "content": "[你的上一条回复因安全审查被拦截，请换一种方式表达。]",
                    })
                    # Run assess_me before reformulation — the model may not
                    # realize its response was blocked; assess_me reframes its
                    # intention for the retry
                    if spec.assess_me_callback is not None:
                        await self._run_assess_callback(spec, messages)
                    empty_content_retries = 0
                    retry_ctx.llm_request_state.record_success()
                    continue
                if spec.assess_me_callback is not None:
                    assess_injected = await self._run_assess_callback(spec, messages)
                    messages_for_model = strip_bypassed_tool_messages(messages)
                    messages_for_model = drop_orphan_tool_results(messages_for_model)
                    messages_for_model = backfill_missing_tool_results(messages_for_model)
                    messages_for_model = split_thinking_messages(messages_for_model, spec.model)
                    _ensure_instructions(messages_for_model, spec)
                    response = await request_finalization_retry(spec, messages_for_model, has_assessment=assess_injected)
                    _llm_request_count += 1
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
                model=spec.model,
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

            # End-of-loop self-assessment — inject assessment context for the
            # next turn (if assess_me finds issues) without overriding the current
            # response. The original final_content always goes to the user.
            if not _end_assess_ran and response.finish_reason != "error" and iteration + 1 < spec.max_iterations:
                _end_assess_ran = True
                assess_result = await self._run_assess_callback(spec, messages)
                if assess_result.needs_revision or assess_result:
                    logger.info(
                        "End-of-loop assess {} (iter={}, session={}) — continuing",
                        "needs revision" if assess_result.needs_revision else "injected context",
                        iteration, spec.session_key or "default",
                    )
                    had_injections = True
                    # Response failed assessment — rollback the last assistant
                    # response while keeping the newly injected guidance.
                    # The callback modifies messages in-place (filters old
                    # assessment messages, appends new ones), so the pre-call
                    # index is stale. Search backward for the last assistant.
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "assistant":
                            messages.pop(i)
                            break
                    _end_assess_ran = False
                    continue

            # Before breaking: check for pending subagents. If any are still
            # running, wait for them (no LLM calls during wait). Subagent
            # results arrive via the injection queue and re-enter the main loop.
            if spec.subagent_running_callback:
                try:
                    has_active = spec.subagent_running_callback() > 0
                except Exception:
                    logger.exception("subagent_running_callback failed at break check")
                    has_active = False
                if has_active:
                    injections = await self._wait_subagents(spec, injection_cycles)
                    if injections:
                        injection_cycles += 1
                        append_injected_messages(messages, injections, assistant_message)
                        _end_assess_ran = False
                        await hook.after_iteration(context)
                        continue
                    # Timeout: subagents still running. Tell the LLM and let it decide.
                    try:
                        remaining = spec.subagent_running_callback()
                    except Exception:
                        remaining = -1
                    if remaining > 0:
                        messages.append({
                            "role": "user",
                            "content": (
                                f"## Instructions\n\n"
                                f"Subagent wait timed out after "
                                f"{spec.subagent_wait_timeout}s — {remaining} subagent(s) "
                                f"still running. Decide how to proceed: cancel hung "
                                f"subagents, wait more, or continue without results."
                            ),
                        })
                        _end_assess_ran = False
                        await hook.after_iteration(context)
                        continue
            _now = time.monotonic()
            logger.info("RUN_Timing: loop_break={:.0f}ms", (_now - _run_t0) * 1000)
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

        # No deferred queue to flush — the tool's pre-send assess callback
        # already handles quality checks synchronously in execute().

        _now = time.monotonic()
        logger.info("RUN_Timing: return={:.0f}ms", (_now - _run_t0) * 1000)
        self._current_spec = None
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
            total_llm_requests=_llm_request_count,
        )

    def _log_tool_call(
        self, session_key: str, iteration: int, turn: int,
        tool_name: str, params: dict[str, Any] | None,
        result: str, success: bool, error: str | None, duration_ms: int | None = None,
    ) -> None:
        if self._db is None or not session_key:
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
        if messages and messages[-1].get("content") == content:
            return
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            existing = messages[-1].get("content", "")
            # Don't overwrite content containing tool_summary markers —
            # _append_turn_to_session needs them to replace tool results.
            if isinstance(existing, str) and "[tool_summary:" in existing:
                messages.append({"role": "assistant", "content": content})
                return
            messages[-1]["content"] = content
            return
        messages.append({"role": "assistant", "content": content})

    async def _check_tool_loop(
        self,
        state: _ToolLoopState,
        tool_calls: list,
        new_events: list,
        messages: list[dict],
        iteration: int,
    ) -> str | None:
        """Check for sustained tool errors, repeated empty results, and param validation loops.

        Detects three patterns:
        1. Same tool + same error repeated → param validation / consistent failure
        2. Any tool errors across consecutive iterations → sustained failure mode
        3. Same tool returning empty/near-empty repeatedly → useless result loop

        Returns None (no action), "assess_me", "compress", or "force_stop".
        """
        if iteration == state.checked_iteration:
            return None
        state.checked_iteration = iteration

        # ── Pattern 1: same tool + same error repeated ──
        tool_errors: list[tuple[str, str]] = []
        empty_results: list[str] = []
        for tc, ev in zip(tool_calls, new_events):
            if ev.get("status") == "error":
                detail = ev.get("detail", "")
                if "Invalid parameters" in detail:
                    tool_errors.append((tc.name, detail))
                else:
                    tool_errors.append((tc.name, detail[:80]))
            # Pattern 3 candidate: very short results (likely empty/uninformative)
            detail = ev.get("detail", "")
            if ev.get("status") == "ok" and len(detail) < 20 and not detail.startswith("Error"):
                empty_results.append(tc.name)

        # Pattern 1: per-tool param validation loop
        if tool_errors:
            # Consolidate: merge same-tool errors
            from collections import Counter
            err_counts: Counter = Counter(f"{name}:{sig[:40]}" for name, sig in tool_errors)
            worst_key, worst_count = err_counts.most_common(1)[0]
            tool_name = worst_key.split(":")[0]
            # Use the original (untruncated) sig for cross-iteration comparison
            error_sig_full = next((sig for name, sig in tool_errors if name == tool_name and f"{name}:{sig[:40]}" == worst_key), "")

            if tool_name == state.tool_name and error_sig_full == state.error_sig:
                state.count += worst_count
            else:
                # Different tool/error → start new tracking (resets level)
                state.tool_name = tool_name
                state.error_sig = error_sig_full
                state.count = worst_count
                state.level = 0

            if state.count >= 3:
                return self._tool_loop_escalate(state)
        else:
            # No errors in this iteration → reset Pattern 1 state
            state.tool_name = ""
            state.error_sig = ""
            state.count = 0
            state.level = 0

        # Pattern 2: consecutive errors across any tools (sustained failure)
        has_any_error = any(ev.get("status") == "error" for ev in new_events)
        if has_any_error:
            state.consecutive_errors += 1
            if state.consecutive_errors == 5:
                logger.info(
                    "Sustained tool failure detected: {} consecutive errors",
                    state.consecutive_errors,
                )
                return "assess_me"
        else:
            state.consecutive_errors = 0

        # Pattern 3: repeated empty results from same tool
        if empty_results:
            et = empty_results[0]
            if et == state.empty_tool:
                state.empty_count += 1
            else:
                state.empty_tool = et
                state.empty_count = 1

            if state.empty_count >= 4:
                logger.info(
                    "Repeated empty result: {} ({}x)", state.empty_tool, state.empty_count,
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Repeated empty result on '{state.empty_tool}'. "
                        f"This tool keeps returning nothing useful — try a different approach.]"
                    ),
                })
                state.empty_count = 0
                return "assess_me"

        return None

    def _tool_loop_escalate(self, state: _ToolLoopState) -> str:
        """Escalate through tool loop recovery levels."""
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
        if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == _PERSISTED_MODEL_ERROR_PLACEHOLDER:
            return
        messages.append({"role": "user", "content": _PERSISTED_MODEL_ERROR_PLACEHOLDER})

    # Backward compatibility — delegate to module functions
    _drop_orphan_tool_results = staticmethod(drop_orphan_tool_results)
    _backfill_missing_tool_results = staticmethod(backfill_missing_tool_results)


def _force_final_response(messages: list[dict], text: str) -> str:
    """Strip the last broken tool-call turn and append a final text response."""
    if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
        messages.pop()
    while messages and messages[-1].get("role") == "tool":
        messages.pop()
    messages.append({"role": "user", "content": text})
    return text


def build_fix_agent_spec(
    *,
    workspace: Path,
    memory_store,
    system_prompt: str,
    user_message: str,
    model: str,
    exec_timeout: int = 120,
    max_iterations: int = 100,
    max_tool_result_chars: int = 10000,
    session_key: str | None = None,
    context_window_tokens: int | None = None,
    history_token_limit: int | None = None,
    compress_trigger_tokens: int | None = None,
) -> AgentRunSpec:
    """Build an AgentRunSpec for the behavior optimization fix sub-agent.

    Registers file/memory/search tools and returns a ready-to-run spec.
    Both assess_me and MemoryExtractor paths use this.
    """
    from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
    from nanobot.agent.tools.memory_search import MemorySearchTool
    from nanobot.agent.tools.search import GlobTool, GrepTool
    from nanobot.agent.tools.shell import ExecTool
    from nanobot.agent.tools.skill_search import SkillSearchTool

    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=workspace))
    tools.register(WriteFileTool(workspace=workspace))
    tools.register(EditFileTool(workspace=workspace))
    tools.register(GlobTool(workspace=workspace))
    tools.register(GrepTool(workspace=workspace))
    tools.register(ExecTool(working_dir=str(workspace), timeout=exec_timeout))
    tools.register(SkillSearchTool(store=memory_store))
    tools.register(MemorySearchTool(store=memory_store))

    return AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        tools=tools,
        model=model,
        max_iterations=max_iterations,
        max_tool_result_chars=max_tool_result_chars,
        session_key=session_key,
        context_window_tokens=context_window_tokens,
        history_token_limit=history_token_limit,
        compress_trigger_tokens=compress_trigger_tokens,
    )


def _ensure_instructions(msgs: list[dict], spec: Any) -> None:
    """Ensure ``## Instructions`` block exists at index 1 (after system prompt).

    Replaces any existing stale copy so callers that bypass the normal per-iteration
    injection (e.g. error-recovery finalization paths) still send fresh instructions
    to the model.
    """
    instr_content = spec.instructions() if callable(spec.instructions) else spec.instructions
    if not instr_content or not msgs:
        return
    instr = {"role": "user", "content": f"## Instructions\n\n{instr_content}"}
    if (len(msgs) > 1
            and msgs[1].get("role") == "user"
            and isinstance(msgs[1].get("content"), str)
            and msgs[1]["content"].startswith("## Instructions")):
        msgs[1] = instr
    else:
        msgs.insert(1, instr)
