"""Message handler classes for AgentLoop."""

from __future__ import annotations

import dataclasses
import time

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.bus.events import OutboundMessage

from nanobot.agent.assess_me import is_assessment_message, is_debug_root_cause_message, contains_suppress_output_marker
from nanobot.agent.context import ContextState, _sanitize_session_key

from nanobot.bus.events import OutboundMessage
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from nanobot.utils.tool_hints import format_single_tool_hint

def _create_bus_progress_callback(loop, msg):
    """Create a bus-based progress callback from message context.

    Derived from msg.channel/chat_id/metadata so progress messages
    can be routed through the bus when no hub-provided on_progress
    callback is available.
    """
    proxy_key: str | None = None
    # System/subagent messages have channel="system" but carry the real origin
    # channel in _origin_channel metadata.  Use it for proxy_key derivation
    # so progress messages can be routed through the correct proxy connection.
    effective_ch = msg.metadata.get("_origin_channel") or msg.channel
    if effective_ch.startswith("proxy:"):
        proxy_key = effective_ch[len("proxy:"):]

    async def _bus_progress(content, *, tool_hint=False, tool_events=None):
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        if proxy_key:
            meta["_proxy_key"] = proxy_key
        # Use _origin_chat_id when present (system/subagent messages carry
        # full qualified chat_id like "proxy:feishu:feishu1:oc_xxx" but the
        # proxy expects just the raw chat_id "oc_xxx").
        effective_chat_id = msg.metadata.get("_origin_chat_id") or msg.chat_id
        parts: list[str] = []
        if tool_events:
            for te in tool_events:
                if not isinstance(te, dict):
                    continue
                phase = te.get("phase", "")
                name = te.get("name", "tool")
                if phase == "end":
                    args = te.get("arguments", {})
                    hint = format_single_tool_hint(name, args) if isinstance(args, dict) and args else name
                    parts.append(f"✅ {hint} completed")
                elif phase == "error":
                    error = te.get("error", "")
                    args = te.get("arguments", {})
                    hint = format_single_tool_hint(name, args) if isinstance(args, dict) and args else name
                    parts.append(f"❌ {hint}: {error}" if error else f"❌ {hint} failed")
        formatted = "\n".join(parts)
        final_content = content
        if formatted:
            final_content = (content + "\n" + formatted) if content else formatted
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=effective_chat_id,
            content=final_content, metadata=meta,
        ))
    return _bus_progress


async def _noop_progress(content: str, *, tool_hint: bool = False, tool_events: list | None = None) -> None:
    """No-op progress callback — subagent dispatch progress must not leak to the user."""
    pass


class SystemMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, on_progress=None, on_stream=None, on_stream_end=None, on_reasoning=None, on_reasoning_end=None, pending_queue=None, extra_hooks=None):
        # Subagent messages arrive on "system" channel from _inject_to_orchestrator,
        # identified by _origin_channel/_origin_chat_id metadata.  Must be resolved
        # before on_progress fallback below.
        is_subagent = bool(msg.metadata.get("_origin_channel"))
        # Bus fallback when no hub-provided on_progress callback
        # Subagent dispatches produce internal orchestrator activity (tool events,
        # full LLM responses) that must NOT leak to the user — use a no-op
        # progress callback so none of it reaches the outbound bus.
        if on_progress is None:
            if is_subagent:
                on_progress = _noop_progress
            else:
                on_progress = _create_bus_progress_callback(self._loop, msg)
        # Prefer origin channel/chat_id from metadata (set by _announce_result)
        # to avoid parsing issues with multi-colon channel values like "proxy:feishu:feishu1".
        if msg.metadata and msg.metadata.get("_origin_channel") and msg.metadata.get("_origin_chat_id"):
            channel = msg.metadata["_origin_channel"]
            chat_id = msg.metadata["_origin_chat_id"]
        else:
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self._loop.lifecycle.prepare(key)
        # For "system" channel (subagent), use channel extracted from chat_id (e.g. "slack").
        # For all other channels (cron, proxy, direct), use msg.channel directly.
        effective_channel = channel if msg.channel == "system" else msg.channel
        self._loop._set_tool_context(effective_channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)

        from nanobot.utils.helpers import estimate_message_tokens
        # Format full history (no budget-based truncation)
        history = session.format_history(include_timestamps=True, timezone=self._loop.context.timezone)

        # Compression check: trigger → compress
        limit = self._loop._history_token_limit
        hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        logger.info(
            "CT_DBG: entry (sysmsg), hist_tokens={}, trigger={}, limit={}",
            hist_tokens, self._loop._compress_trigger_tokens, limit,
        )
        if hist_tokens > self._loop._compress_trigger_tokens:
            from nanobot.agent.compress import (
                MIN_KEEP_TURNS,
                apply_compress_event,
                compress_session,
            )

            logger.info("CT_DBG: compress_session start (sysmsg)")
            history, event = await compress_session(
                session, history,
                limit=limit, min_keep_turns=MIN_KEEP_TURNS,
            )
            logger.info("CT_DBG: compress_session done (sysmsg, summary={})", bool(event.summary))
            logger.info("CT_DBG: apply_compress_event start (sysmsg)")
            apply_compress_event(session, event, db=self._loop._db)
            logger.info("CT_DBG: apply_compress_event done (sysmsg)")

        hist_tokens_after = sum(estimate_message_tokens(m) for m in history) if history else 0
        hist_turns_after = sum(1 for m in history if m.get("role") == "assistant")
        logger.info(
            "HISTORY_DBG: key={}, history_msgs={}, history_turns={}, history_tokens={}",
            key, len(history), hist_turns_after, hist_tokens_after,
        )

        cs = ContextState(
            tool_definitions=self._loop.tools.get_definitions(),
            current_iteration=self._loop._current_iteration,
            max_iterations=self._loop.max_iterations,
        )
        if is_subagent:
            history = list(history)
            suffix = f"_{_sanitize_session_key(key)}" if key else ""
            history.append({"role": "user", "content": f"## Subagent Result — 按 Orchestration Guide 处理\n\n{msg.content.strip()}\n\n按 Orchestration Guide 的 Trigger-Action Rules 处理。\n\n### 决策原则\n- 方案选择、优先级、下一步 → 直接决策，不需要问用户\n- 可回退的决策自己做，错了用户会纠正\n- 只有 Safety/Privacy 规则定义的操作才需要确认"})
            current_message = ""
        else:
            current_message = msg.content
        messages = self._loop.context.build_messages(
            history=history,
            current_message=current_message,
            channel=effective_channel,
            chat_id=chat_id,
            current_role="user",
            context_state=cs,
            session_key=key,
        )
        final_content, _, all_msgs, stop_reason, _, initial_msg_count, _total_llm_requests = await self._loop._run_agent_loop(messages, on_progress=on_progress, on_stream=on_stream, on_stream_end=on_stream_end, on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end, session=session, channel=effective_channel, chat_id=chat_id, message_id=msg.metadata.get("message_id"), metadata=msg.metadata, session_key=key, pending_queue=pending_queue, extra_hooks=extra_hooks)
        # 不剥离 assess_me/DRC — _append_turn_to_session 会在 append 时过滤。
        # 预剥离会缩短 all_msgs 但 initial_msg_count 不变，导致索引错位。
        session.metadata["llm_request_count"] = session.metadata.get("llm_request_count", 0) + _total_llm_requests
        if is_subagent and self._loop._persist_subagent_followup(session, msg):
            self._loop.sessions.save(session)
        self._loop._append_turn_to_session(session, all_msgs, initial_msg_count if is_subagent else initial_msg_count - 1)
        self._loop.lifecycle.finalize(session)

        # Subagent dispatches run inside the orchestrator's bus wait loop.
        # Their output (tool events, LLM responses) is internal processing
        # and must NOT be published to the outbound queue, which feeds into
        # the user-facing delivery path (on_stream/on_progress in
        # single-message mode, or _consume_outbound in interactive mode).
        # Return None so run_dispatch skips bus.publish_outbound.
        if is_subagent:
            return None

        content = final_content or "Background task completed."
        buttons: list = []
        outbound_metadata: dict[str, Any] = {}
        if effective_channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        return OutboundMessage(channel=effective_channel, chat_id=chat_id, content=content, buttons=buttons, metadata=outbound_metadata,
                               tools_used=self._loop._last_tools_used, usage=self._loop.last_usage, stop_reason=stop_reason,
                               error=self._loop._last_error)


class UserMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, session_key, on_progress, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None, extra_hooks=None):

        if msg.media:
            # All media (images, files, etc.) → save to workspace for tool access
            import shutil
            ws = self._loop.workspace
            ws.mkdir(parents=True, exist_ok=True)
            labels: list[str] = []
            dest_paths: list[str] = []
            for p in msg.media:
                pa = Path(p)
                if not pa.is_file():
                    continue
                dest = ws / pa.name
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    counter = 1
                    while dest.exists():
                        dest = ws / f"{stem}_{counter}{suffix}"
                        counter += 1
                shutil.copy2(str(pa), str(dest))
                labels.append(f"{pa.name} → {dest}")
                dest_paths.append(str(dest))

            # Inform LLM what was received, with real paths so it can read them
            ref = f"[用户发送了: {'、'.join(labels)}]"
            new_content = f"{msg.content}\n\n{ref}" if msg.content else ref
            # Keep dest_paths so downstream _build_user_content can inject image_url blocks
            msg = dataclasses.replace(msg, content=new_content, media=dest_paths)

        # Inject quoted message context into content
        quoted = msg.metadata.get("quoted_message", "")
        if quoted:
            msg = dataclasses.replace(
                msg,
                content=f"[Quoting the following message]\n{quoted}\n\n---\n{msg.content}",
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Stage 0: fast-path command dispatch — skip heavy session prep for commands
        key = session_key or msg.session_key
        session = self._loop.sessions.get_or_create(key)
        if result := await self._dispatch_command(msg, session, key):
            return result

        # Stage 1: session preparation (checkpoint restore & history loading)
        session, pending, history, channel, chat_id, key = self._prepare_session(msg, session_key)

        # Stage 1a: proactive check
        if msg.metadata.get("proactive_check"):
            history = list(history)
            history.append({"role": "user", "content": msg.content})
            msg = dataclasses.replace(msg, content="")

        # Reset iteration counter — each new turn starts at 0
        self._loop._current_iteration = 0

        # Stage 1.5: compression check — if formatted history exceeds trigger, compress
        from nanobot.utils.helpers import estimate_message_tokens
        _hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        logger.info(
            "CT_DBG: stage=entry, hist_tokens={}, trigger={}, limit={}",
            _hist_tokens, self._loop._compress_trigger_tokens, self._loop._history_token_limit,
        )
        if _hist_tokens > self._loop._compress_trigger_tokens:
            from nanobot.agent.compress import (
                MIN_KEEP_TURNS,
                apply_compress_event,
                compress_session,
            )

            logger.info("CT_DBG: compress_session start")
            history, event = await compress_session(
                session, history,
                limit=self._loop._history_token_limit, min_keep_turns=MIN_KEEP_TURNS,
            )
            logger.info("CT_DBG: compress_session done (summary={})", bool(event.summary))

            logger.info("CT_DBG: apply_compress_event start")
            apply_compress_event(session, event, db=self._loop._db)
            logger.info("CT_DBG: apply_compress_event done")

        # Stage 2: tool context
        self._loop._set_tool_context(msg.channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        logger.info("STAGE_DBG: tool context done")

        # Stage 3: build initial messages
        initial_messages, pending_ask_id = self._build_initial_messages(msg, history, pending, session, key)
        logger.info("STAGE_DBG: _build_initial_messages done ({} messages)", len(initial_messages))

        # Stage 4: callbacks
        on_progress_final = on_progress or self._make_bus_progress_callback(msg)
        on_retry_wait = self._make_retry_wait_callback(msg, on_progress_final)
        logger.info("STAGE_DBG: callbacks done")

        # Stage 5: persist user message before loop runs
        user_persisted_early = False
        if not msg.ephemeral:
            user_persisted_early = self._persist_user_message_early(session, msg, pending_ask_id)
        logger.info("STAGE_DBG: persist done (ephemeral={}, early={})", msg.ephemeral, user_persisted_early)

        # Stage 6: run agent loop
        logger.info("STAGE_DBG: entering _run_agent_loop")
        final_content, _, all_msgs, stop_reason, had_injections, initial_msg_count, _total_llm_requests = await self._loop._run_agent_loop(
            initial_messages,
            on_progress=on_progress_final,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_reasoning=on_reasoning,
            on_reasoning_end=on_reasoning_end,
            on_retry_wait=on_retry_wait,
            session=session,
            channel=msg.channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            pending_queue=pending_queue,
            extra_hooks=extra_hooks,
        )
        # 不剥离 assess_me/DRC — _append_turn_to_session/_finalize_turn 内部会过滤。
        # 预剥离会缩短 all_msgs 但 initial_msg_count 不变，导致索引错位。

        # Track time from _run_agent_loop return to _build_outbound
        _t_loop_return = time.time()
        logger.debug("TIMING_DBG: _run_agent_loop returned")

        # Stage 8 (first): build outbound — send response to user ASAP
        _t1 = time.time()
        _gap = _t1 - _t_loop_return
        if _gap > 1:
            logger.info("TIMING: _run_agent_loop return → _build_outbound gap={:.1f}s", _gap)
        result = self._build_outbound(msg, final_content, stop_reason, all_msgs, had_injections, on_stream)
        _t2 = time.time()
        if _t2 - _t1 > 0.5:
            logger.info("TIMING: _build_outbound took {:.1f}s", _t2 - _t1)

        # Stage 7 (after): finalize — save, file cap, recovery clear (response already sent)
        if msg.ephemeral:
            _t1 = time.time()
            self._loop.lifecycle.finalize_ephemeral(session)
            _t2 = time.time()
            if _t2 - _t1 > 1:
                logger.info("TIMING: finalize_ephemeral took {:.1f}s", _t2 - _t1)
        else:
            _t1 = time.time()
            await self._finalize_turn(session, all_msgs, initial_msg_count, user_persisted_early, final_content, _total_llm_requests)
            _t2 = time.time()
            if _t2 - _t1 > 1:
                logger.info("TIMING: _finalize_turn took {:.1f}s", _t2 - _t1)
        return result

    def _prepare_session(self, msg, session_key):
        """Restore checkpoints, return session + derived context."""
        key = session_key or msg.session_key
        session = self._loop.lifecycle.prepare(key)

        # DEBUG: log actual session state for diagnosing session-reset-after-reconnect
        session_msg_count = len(session.messages)
        session_keys = list(self._loop.sessions._cache.keys()) if hasattr(self._loop.sessions, '_cache') else []
        in_cache = key in session_keys if hasattr(self._loop.sessions, '_cache') else 'unknown'
        # Count assistant messages = turns
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        # Estimate total history tokens
        from nanobot.utils.helpers import estimate_message_tokens
        total_tokens = sum(estimate_message_tokens(m) for m in session.messages) if session.messages else 0
        logger.info(
            "SESSION_DBG: key={}, msgs={}, turns={}, tokens={}, in_cache={}, cache_keys_count={}",
            key, session_msg_count, assistant_count, total_tokens, in_cache, len(session_keys),
        )

        pending = None
        from nanobot.utils.helpers import estimate_message_tokens

        # Format full history (no budget-based truncation)
        history = session.format_history(include_timestamps=True, timezone=self._loop.context.timezone)

        # Crash recovery: if session was loaded from DB with a summary, inject it
        # at the front of history so the LLM knows the compressed context.
        # Use a persisted metadata key to detect re-injection across DB reloads.
        last_summary = getattr(session, '_last_summary', None)
        if (last_summary
            and not getattr(session, '_summary_injected', False)
            and session.metadata.get("_summary_injected_key") != last_summary):
            from nanobot.agent.compress import make_summary_pair
            summary_msgs = make_summary_pair(last_summary)
            history = summary_msgs + history
            session._summary_injected = True
            session.metadata["_summary_injected_key"] = last_summary
            logger.info("Crash recovery summary injected for session {}", key)

        # Log what format_history actually returned
        hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        hist_turns = sum(1 for m in history if m.get("role") == "assistant")
        logger.info(
            "HISTORY_DBG: key={}, history_msgs={}, history_turns={}, history_tokens={}",
            key, len(history), hist_turns, hist_tokens,
        )

        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        return session, pending, history, channel, chat_id, key

    async def _dispatch_command(self, msg, session, key):
        """Run command dispatch, return result if handled."""
        from nanobot.command import CommandContext
        ctx = CommandContext(msg=msg, session=session, key=key, raw=msg.content.strip(), loop=self._loop)
        # Priority commands — re-dispatched stop/new now carry plain-text
        # content (e.g. "stop.") so they won't match is_priority and will
        # fall through to dispatch → interceptors → cmd_unknown returns
        # None (no "/" prefix) → LLM processes the message normally.
        result = await self._loop.commands.dispatch_priority(ctx)
        if result:
            return result
        return await self._loop.commands.dispatch(ctx)

    def _build_initial_messages(self, msg, history, pending, session, key=None):
        """Build the initial message list for the agent loop."""
        cs = ContextState(
            tool_definitions=self._loop.tools.get_definitions(),
            current_iteration=self._loop._current_iteration,
            max_iterations=self._loop.max_iterations,
        )
        initial_messages = self._loop.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=self._loop._runtime_chat_id(msg),
            context_state=cs,
            session_key=key,
        )
        return initial_messages, None

    def _make_bus_progress_callback(self, msg):
        return _create_bus_progress_callback(self._loop, msg)

    def _make_retry_wait_callback(self, msg, on_progress=None):
        async def _on_retry_wait(content):
            if content in {"empty_response", "length_recovery"}:
                logger.debug("Retry wait: skipping callback for category '{}'", content)
                return
            if on_progress:
                await on_progress(content, tool_events=None)
                return
            meta = dict(msg.metadata or {})
            meta["_retry_wait"] = True
            await self._loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=content, metadata=meta,
            ))
        return _on_retry_wait

    def _persist_user_message_early(self, session, msg, pending_ask_id):
        """Add user message to session before the loop runs (persisted at finalize)."""
        return self._loop.lifecycle.persist_user_message(session, msg, pending_ask_id)

    async def _finalize_turn(self, session, all_msgs, initial_msgs_count, user_persisted_early, final_content, total_llm_requests=0):
        """Save turn, enforce file cap, clear recovery state."""
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Rebuild session.messages from all_msgs to persist compressed history.
        # When proactive compression compresses the history portion of all_msgs,
        # that compressed state must be written back to session.messages so the
        # next turn loads the compressed version instead of the full uncompressed
        # history.
        # Detect instructions injected at index 1 (from runner iteration loop).
        _history_start = 1
        if (len(all_msgs) > 1
                and all_msgs[1].get("role") == "user"
                and isinstance(all_msgs[1].get("content"), str)
                and all_msgs[1]["content"].startswith("## Instructions")):
            _history_start = 2

        # Find current-turn user message dynamically by scanning from the end.
        # After mid-run compression, initial_msgs_count may be inflated with
        # interleaved assistant/tool messages from earlier iterations, making
        # the old formula (initial_msgs_count - 1) unreliable.  Scanning
        # backwards for the last non-assessment user message is always correct.
        _user_msg_idx = -1
        for i in range(len(all_msgs) - 1, -1, -1):
            if (all_msgs[i].get("role") == "user"
                    and not is_assessment_message(all_msgs[i])
                    and not is_debug_root_cause_message(all_msgs[i])):
                _user_msg_idx = i
                break
        if _user_msg_idx < 0:
            _user_msg_idx = _history_start

        save_skip = _user_msg_idx + (1 if user_persisted_early else 0)

        # History portion (post-compression if compression happened) excluding the current user message
        history_slice = list(all_msgs[_history_start:_user_msg_idx])
        # Current turn messages
        current_slice = list(all_msgs[save_skip:])

        # Save the early-persisted user message before clearing session.messages
        _persisted_user_msg = None
        if user_persisted_early and session.messages:
            _persisted_user_msg = dict(session.messages[-1])

        session.messages = []
        self._loop._append_turn_to_session(session, history_slice, 0)
        if user_persisted_early:
            if _persisted_user_msg:
                session.messages.append(_persisted_user_msg)
            else:
                # Fallback: use from all_msgs (loses timestamp/extra fields)
                session.messages.append(dict(all_msgs[_user_msg_idx]))
        self._loop._append_turn_to_session(session, current_slice, 0)

        # Mark summary as already injected so crash recovery doesn't re-inject
        # a summary that's already in the compressed history.
        _last_summary = getattr(session, '_last_summary', None)
        if _last_summary:
            session.metadata["_summary_injected_key"] = _last_summary

        # Track persistent LLM request count (survives compression)
        if total_llm_requests:
            session.metadata["llm_request_count"] = (
                session.metadata.get("llm_request_count", 0) + total_llm_requests
            )

        # Track persistent assistant turn count (survives compression)
        new_assistant_msgs = sum(
            1 for m in all_msgs[save_skip:]
            if m.get("role") == "assistant"
        )
        session.metadata["assistant_turn_count"] = (
            session.metadata.get("assistant_turn_count", 0) + new_assistant_msgs
        )

        # Lifecycle: cap, clear checkpoints, save (persists metadata including counters)
        self._loop.lifecycle.finalize(session)

    def _build_outbound(self, msg, final_content, stop_reason, all_msgs, had_injections, on_stream):
        """Format the final OutboundMessage for the user.

        Suppression: all four assess_me markers suppress text output (but tool_calls run).
        "继续推进原始任务" and "无需回应此消息" have identical effect — both mean
        "stop arguing/explaining, just work". assess_me re-evaluates on the next turn.
        """
        # Suppress check: unconditionally check ALL messages for assessment+marker.
        # The had_injections flag is not a precondition — suppress must apply
        # whenever an assessment message with a suppress marker is present,
        # regardless of whether other injections exist.
        _suppress_detected = False
        if all_msgs:
            for m in reversed(all_msgs):
                if is_assessment_message(m):
                    content = m.get("content", "")
                    if contains_suppress_output_marker(content):
                        logger.info("Suppressing response: assess_me marked as无需回应")
                        _suppress_detected = True
                        break
            # Fallback: when had_injections is True, check all messages for suppress marker
            # This handles cases where is_assessment_message may not recognize the message format.
            if not _suppress_detected and had_injections:
                for m in reversed(all_msgs):
                    if m.get("role") == "user" and isinstance(m.get("content"), str):
                        if contains_suppress_output_marker(m.get("content", "")):
                            logger.info("Suppressing response: had_injections + suppress marker found")
                            _suppress_detected = True
                            break

        if _suppress_detected:
            final_content = ""
        elif final_content is None:
            final_content = ""
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True
        error = self._loop._last_error
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_content, metadata=meta,
                               tools_used=self._loop._last_tools_used, usage=self._loop.last_usage, stop_reason=stop_reason,
                               error=error)
