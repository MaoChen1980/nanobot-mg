"""Message handler classes for AgentLoop."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage, OutboundMessage

from nanobot.agent.context import ContextState
from nanobot.bus.events import OutboundMessage
from nanobot.session.manager import Session
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from nanobot.agent.memory_extractor import MemoryExtractor
from nanobot.agent.tools.message import MessageTool


def _has_recent_user_response(session, content, message_id=""):
    """Check if session already has a matching user message with an assistant response.

    Matches by ``message_id`` first (stored as ``_message_id`` in session messages),
    falling back to content matching for messages without an ID.
    """
    for i in range(len(session.messages) - 1, -1, -1):
        role = session.messages[i].get("role")
        if role in ("assistant", "tool"):
            continue
        if role == "user":
            stored_msg_id = session.messages[i].get("_message_id", "") or ""
            if message_id and stored_msg_id and stored_msg_id == message_id:
                has_assistant = any(
                    m.get("role") == "assistant" and m.get("content")
                    for m in session.messages[i:]
                )
                return has_assistant
            if not message_id and not stored_msg_id:
                stored = session.messages[i].get("content", "")
                if stored.strip() == content.strip():
                    return any(
                        m.get("role") == "assistant" and m.get("content")
                        for m in session.messages[i:]
                    )
        break
    return False


def _has_context_window_error(content: str | None) -> bool:
    """Check if an LLM error response indicates the context window was exceeded."""
    if not content:
        return False
    lowered = content.lower()
    markers = (
        "context window",
        "maximum context",
        "prompt is too long",
        "too many tokens",
        "token limit",
        "context length",
    )
    return any(m in lowered for m in markers)


class SystemMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None):
        from nanobot.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self._loop.lifecycle.prepare(key)
        pending = None
        is_subagent = msg.sender_id == "subagent"
        # Subagent result is NOT persisted before the LLM loop, because:
        # _persist_subagent_followup saves it as an "assistant" message, and
        # get_history → build_messages would put it in history as the last
        # assistant message with no user message after it. The LLM then sees
        # the subagent result as its own past output and produces nothing.
        # Instead, pass the subagent result as a user message (prompting LLM
        # to respond), then persist after the loop for durability.
        # For "system" channel (subagent), use channel extracted from chat_id (e.g. "slack").
        # For all other channels (cron, proxy, direct), use msg.channel directly.
        effective_channel = channel if msg.channel == "system" else msg.channel
        self._loop._set_tool_context(effective_channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        from nanobot.utils.helpers import estimate_message_tokens
        raw_budget = self._loop._compute_history_budget()
        tool_defs = self._loop.tools.get_definitions()
        sys_prompt = self._loop.context.build_system_prompt(channel=msg.channel, tool_definitions=tool_defs)
        sys_tokens = estimate_message_tokens({"role": "system", "content": sys_prompt})
        adjusted = raw_budget - sys_tokens
        if adjusted < 1024:
            adjusted = raw_budget
        self._loop._last_adjusted_budget = adjusted
        # Compress session if over budget before building LLM prompt
        self._loop._compress_if_needed(session)
        history = session.get_history(max_turns=0, max_messages=0, max_tokens=max(128, adjusted), include_timestamps=True, timezone=self._loop.context.timezone)
        hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        hist_turns = sum(1 for m in history if m.get("role") == "assistant")
        logger.info(
            "HISTORY_DBG: key={}, budget_adjusted={}, history_msgs={}, history_turns={}, history_tokens={}",
            key, adjusted, len(history), hist_turns, hist_tokens,
        )
        cs = ContextState(
            tool_definitions=self._loop.tools.get_definitions(),
            current_iteration=self._loop._current_iteration,
            max_iterations=self._loop.max_iterations,
            context_window_tokens=self._loop.context_window_tokens or None,
            history_budget_tokens=adjusted or None,
        )
        messages = self._loop.context.build_messages(
            history=history,
            current_message=msg.content,
            channel=effective_channel,
            chat_id=chat_id,
            current_role="user",
            context_state=cs,
        )
        final_content, _, all_msgs, stop_reason, _ = await self._loop._run_agent_loop(messages, on_stream=on_stream, on_stream_end=on_stream_end, on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end, session=session, channel=effective_channel, chat_id=chat_id, message_id=msg.metadata.get("message_id"), metadata=msg.metadata, session_key=key, pending_queue=pending_queue)
        msgs_count = len(messages)
        # Persist subagent result after the LLM loop so it appears in session
        # before the LLM's response (correct chronological order).
        if is_subagent and self._loop._persist_subagent_followup(session, msg):
            self._loop.sessions.save(session)
        self._loop._append_turn_to_session(session, all_msgs, msgs_count if is_subagent else msgs_count - 1)
        self._loop.lifecycle.finalize(session)
        options = ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else []
        import re
        if final_content:
            final_content = re.sub(
                r'^(?:\[Message Time: [^\]]*\]|====== Message Time: [^=]+ ======)\s*\n?',
                '',
                final_content,
            )
        content, buttons = ask_user_outbound(final_content or "Background task completed.", options, effective_channel)
        outbound_metadata: dict[str, Any] = {}
        if effective_channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        return OutboundMessage(channel=effective_channel, chat_id=chat_id, content=content, buttons=buttons, metadata=outbound_metadata)


class UserMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, session_key, on_progress, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None):
        from nanobot.agent.tools.ask import pending_ask_user_id, ask_user_tool_result_messages

        if msg.media:
            # All media (images, files, etc.) → save to workspace, no auto-processing
            import shutil
            ws = self._loop.workspace
            ws.mkdir(parents=True, exist_ok=True)
            labels: list[str] = []
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

            # Inform LLM what was received, with real paths so it can read them
            ref = f"[用户发送了: {'、'.join(labels)}]"
            new_content = f"{msg.content}\n\n{ref}" if msg.content else ref
            msg = dataclasses.replace(msg, content=new_content, media=[])

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

        # Stage 0a: Re-dispatch guard — check BEFORE checkpoint restore so we
        # don't pollute the session with recovered messages on a duplicate dispatch.
        # Session already has this user message with an assistant response = prior
        # dispatch completed, skip this one entirely.
        if _has_recent_user_response(session, msg.content, msg.metadata.get("message_id", "")):
            logger.info("Re-dispatch detected for session {} (msg='{}...'), skipping", key, msg.content[:40])
            return None

        # Retry loop for context-window-exceeded errors.  When the session
        # exceeds the model's actual context limit (e.g. MiniMax with 200K
        # vs config with 400K), the LLM API rejects the request.  Budget
        # per retry = base * 0.7^n (base = min(config_window, actual_prompt_size)).
        _MAX_CW_RETRIES = 3
        user_persisted_early = False

        for cw_attempt in range(_MAX_CW_RETRIES + 1):
            if cw_attempt > 0:
                # Clean up session state left by the previous attempt:
                # recovery markers, the early-persisted user message, and
                # any runtime checkpoint from a partial agent run.
                self._loop._recovery.clear_pending_user_turn(session)
                self._loop._recovery.clear_runtime_checkpoint(session)
                if user_persisted_early and session.messages and session.messages[-1].get("role") == "user":
                    session.messages.pop()
                user_persisted_early = False


                logger.warning("Context window retry {}/{} for session {}", cw_attempt, _MAX_CW_RETRIES, key)

            # Stage 1: session preparation (checkpoint restore & history loading)
            session, pending, history, channel, chat_id, key, budget_adjusted, context_window = self._prepare_session(msg, session_key)

            # Fix 1: Reset iteration counter — each new turn starts at 0
            self._loop._current_iteration = 0

            # Stage 2: tool context
            self._loop._set_tool_context(msg.channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
            self._maybe_start_message_tool()

            # Stage 3: build initial messages
            initial_messages, pending_ask_id = self._build_initial_messages(msg, history, pending, session, budget_adjusted, context_window)
            initial_msgs_count = len(initial_messages)

            # Stage 4: callbacks
            on_progress_final = on_progress or self._make_bus_progress_callback(msg)
            on_retry_wait = self._make_retry_wait_callback(msg)

            # Stage 5: persist user message before loop runs (skip on retry — already in session)
            if cw_attempt == 0 and not msg.ephemeral:
                user_persisted_early = self._persist_user_message_early(session, msg, pending_ask_id)

            # Stage 6: run agent loop
            final_content, _, all_msgs, stop_reason, had_injections = await self._loop._run_agent_loop(
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
            )

            # Retry on context-window-exceeded — compress & await summary first, then retry
            if stop_reason == "error" and _has_context_window_error(final_content):
                if cw_attempt < _MAX_CW_RETRIES:
                    # Before retrying: trigger compression & await summary so tokens
                    # drop before rebuilding the prompt with (possibly reduced) budget.
                    self._loop._compress_if_needed(session)
                    await self._loop._apply_and_recompress(session)
                    continue
                logger.error("Context window retries exhausted for session {}", key)

            # Success or non-retryable error — exit retry loop
            break

        # Stage 7: finalize — save, file cap, recovery clear, background schedule
        if msg.ephemeral:
            # Ephemeral messages (e.g. heartbeat) skip history persistence,
            # but still clear any runtime checkpoint the loop may have set.
            self._loop.lifecycle.finalize_ephemeral(session)
        else:
            await self._finalize_turn(session, all_msgs, initial_msgs_count, user_persisted_early, final_content)

        # Stage 8: build outbound response
        return self._build_outbound(msg, final_content, stop_reason, all_msgs, had_injections, on_stream)

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
        raw_budget = self._loop._compute_history_budget()
        tool_defs = self._loop.tools.get_definitions()
        sys_prompt = self._loop.context.build_system_prompt(channel=msg.channel, tool_definitions=tool_defs)
        sys_tokens = estimate_message_tokens({"role": "system", "content": sys_prompt})
        adjusted = raw_budget - sys_tokens
        if adjusted < 1024:
            adjusted = raw_budget
        self._loop._last_adjusted_budget = adjusted
        # Compress session if over budget before building LLM prompt
        self._loop._compress_if_needed(session)
        history = session.get_history(max_turns=0, max_messages=0, max_tokens=max(128, adjusted), include_timestamps=True, timezone=self._loop.context.timezone)
        # Log what get_history actually returned
        hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        hist_turns = sum(1 for m in history if m.get("role") == "assistant")
        logger.info(
            "HISTORY_DBG: key={}, budget_adjusted={}, history_msgs={}, history_turns={}, history_tokens={}",
            key, adjusted, len(history), hist_turns, hist_tokens,
        )

        # Fallback 1: If history is empty but session has messages, try max_tokens=0
        if not history and session.messages:
            logger.warning(
                "get_history returned empty for session {} ({} msgs, {} turns, {} tokens, budget={}, "
                "sys_tokens={}), falling back to max_tokens=0",
                key, len(session.messages), assistant_count, total_tokens, adjusted, sys_tokens,
            )
            history = session.get_history(max_turns=80, max_tokens=0, include_timestamps=True, timezone=self._loop.context.timezone)

        if not history and session.messages:
            logger.error(
                "get_history returned empty even with max_tokens=0 for session {} "
                "({} msgs, {} turns, {} tokens), falling back to raw session.messages",
                key, len(session.messages), assistant_count, total_tokens,
            )
            # Last-resort fallback: manually build history entries from raw messages.
            for m in session.messages:
                entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
                for k in ("tool_calls", "tool_call_id", "name", "reasoning_content", "reasoning_details", "thinking_blocks"):
                    if k in m:
                        entry[k] = m[k]
                history.append(entry)

        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        return session, pending, history, channel, chat_id, key, adjusted, self._loop.context_window_tokens

    async def _dispatch_command(self, msg, session, key):
        """Run command dispatch, return result if handled."""
        from nanobot.command import CommandContext
        ctx = CommandContext(msg=msg, session=session, key=key, raw=msg.content.strip(), loop=self._loop)
        # Priority commands (e.g. /stop, /restart) are checked before the
        # dispatch lock in the bus loop path; for direct/proxy messages we
        # must check them here too since they aren't in the regular dispatch.
        result = await self._loop.commands.dispatch_priority(ctx)
        if result:
            return result
        return await self._loop.commands.dispatch(ctx)

    def _maybe_start_message_tool(self):
        """Notify message tool that a turn has started."""
        if message_tool := self._loop.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

    def _build_initial_messages(self, msg, history, pending, session, budget_adjusted=0, context_window=0):
        """Build the initial message list for the agent loop."""
        from nanobot.agent.tools.ask import pending_ask_user_id, ask_user_tool_result_messages
        pending_ask_id = pending_ask_user_id(history)
        if pending_ask_id:
            initial_messages = ask_user_tool_result_messages(
                self._loop.context.build_system_prompt(channel=msg.channel),
                history,
                pending_ask_id,
                msg.content,
            )
        else:
            cs = ContextState(
                tool_definitions=self._loop.tools.get_definitions(),
                current_iteration=self._loop._current_iteration,
                max_iterations=self._loop.max_iterations,
                context_window_tokens=context_window or None,
                history_budget_tokens=budget_adjusted or None,
            )
            initial_messages = self._loop.context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=self._loop._runtime_chat_id(msg),
                context_state=cs,
            )
        return initial_messages, pending_ask_id

    def _make_bus_progress_callback(self, msg):
        async def _bus_progress(content, *, tool_hint=False, tool_events=None):
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            if tool_events:
                meta["_tool_events"] = tool_events
            await self._loop.bus.publish_outbound(OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta))
        return _bus_progress

    def _make_retry_wait_callback(self, msg):
        async def _on_retry_wait(content):
            meta = dict(msg.metadata or {})
            meta["_retry_wait"] = True
            await self._loop.bus.publish_outbound(OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta))
        return _on_retry_wait

    def _persist_user_message_early(self, session, msg, pending_ask_id):
        """Add user message to session before the loop runs (persisted at finalize)."""
        return self._loop.lifecycle.persist_user_message(session, msg, pending_ask_id)

    async def _finalize_turn(self, session, all_msgs, initial_msgs_count, user_persisted_early, final_content):
        """Save turn, compress context via LLM summarization, enforce file cap, clear recovery state."""
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE
        # skip: system prompt (1) + retained_history + user message if already in session
        # initial_msgs_count = 1 + len(retained_history) + 1
        save_skip = initial_msgs_count if user_persisted_early else initial_msgs_count - 1
        self._loop._append_turn_to_session(session, all_msgs, save_skip)

        # Safety: clear stale pending_compress tags if no background task running
        pending = getattr(self._loop, '_pending_compression', None)
        if pending is None:
            stale = [m for m in session.messages if m.get("status") == "pending_compress"]
            if stale:
                for m in stale:
                    m.pop("status", None)
                logger.warning("Cleared {} stale pending_compress tags in finalize (no bg task)", len(stale))

        # Apply pending compression summary if background task completed
        if pending is not None and pending.done():
            try:
                summary = pending.result()
                pending_msgs = [m for m in session.messages if m.get("status") == "pending_compress"]
                if summary and pending_msgs:
                    # Archive originals to history, then replace with summary pair
                    self._loop.context.memory.condense_session_to_history(pending_msgs)
                    ts = pending_msgs[0].get("timestamp", datetime.now(timezone.utc).isoformat())
                    summary_pair = [
                        {"role": "assistant", "content": summary, "timestamp": ts, "status": "synthetic"},
                        {"role": "user", "content": "ok", "timestamp": ts, "status": "synthetic"},
                    ]
                    remaining = [m for m in session.messages if m.get("status") != "pending_compress"]
                    session.messages = list(summary_pair) + remaining
                    logger.info("Applied background summary, archived {} pending msgs for session {}", len(pending_msgs), session.key)
                elif not summary:
                    logger.warning("Background summary returned empty, clearing pending_compress flags")
                    for m in session.messages:
                        m.pop("status", None) if m.get("status") == "pending_compress" else None
            except Exception as e:
                logger.warning("Background summary task failed, restoring messages: {}", e)
                for m in session.messages:
                    if m.get("status") == "pending_compress":
                        m.pop("status", None)
            finally:
                self._loop._pending_compression = None

        # Lifecycle: cap, clear checkpoints, save (trim handled by _compress_if_needed)
        self._loop.lifecycle.finalize(session)

        # .pt save: every N turns, using session assistant count (persists across restarts)
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        if assistant_count > 0 and assistant_count % self._loop._pt_save_interval == 0:
            MemoryExtractor.save_prompt_snapshot(all_msgs, self._loop.prompts_dir, session.key)

    def _build_outbound(self, msg, final_content, stop_reason, all_msgs, had_injections, on_stream):
        """Format the final OutboundMessage for the user."""
        import re
        from nanobot.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        if (mt := self._loop.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None
        if final_content is None:
            final_content = ""
        # Strip [Message Time: ...] / ====== Message Time: ... ====== prefix that the LLM may have mimicked from history context
        final_content = re.sub(
            r'^(?:\[Message Time: [^\]]*\]|====== Message Time: [^=]+ ======)\s*\n?',
            '',
            final_content,
        )
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        final_content, buttons = ask_user_outbound(
            final_content,
            ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else [],
            msg.channel,
        )
        if on_stream is not None and stop_reason not in {"ask_user", "error"}:
            meta["_streamed"] = True
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_content, metadata=meta, buttons=buttons)
