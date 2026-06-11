"""Message handler classes for AgentLoop."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.bus.events import OutboundMessage

from nanobot.agent.context import ContextState
from nanobot.bus.events import OutboundMessage
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from nanobot.agent.memory_extractor import MemoryExtractor
from nanobot.agent.tools.message import MessageTool

_STALE_MESSAGE_MINUTES = 20


def _has_stale_duplicate(session, message_id: str) -> bool:
    """Check if a message with the same ID was already processed long ago.

    Only matches by ``message_id`` — no content matching.
    Only returns True if the original message is older than ``_STALE_MESSAGE_HOURS``.
    """
    if not message_id:
        return False
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_MESSAGE_MINUTES)
    for i in range(len(session.messages) - 1, -1, -1):
        role = session.messages[i].get("role")
        if role in ("assistant", "tool"):
            continue
        if role == "user":
            stored_id = session.messages[i].get("_message_id", "") or ""
            if stored_id == message_id:
                ts = session.messages[i].get("timestamp", "")
                try:
                    msg_time = datetime.fromisoformat(ts)
                    if msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    msg_time = datetime.now(timezone.utc)
                stale = msg_time < cutoff
                return stale
        break
    return False


class SystemMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None):
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
        # Subagent messages arrive on "system" channel from _inject_to_orchestrator,
        # identified by _origin_channel/_origin_chat_id metadata.
        is_subagent = bool(msg.metadata.get("_origin_channel"))
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
        if hist_tokens > self._loop._compress_trigger_tokens:
            from nanobot.agent.compress import (
                apply_compress_event, compress_session, MIN_KEEP_TURNS,
            )

            history, event = await compress_session(
                session, history,
                limit=limit, min_keep_turns=MIN_KEEP_TURNS,
            )
            apply_compress_event(session, event, db=self._loop._db)

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
            # Inject two messages so the LLM sees a natural self-reminder +
            # directive sequence, all API-compliant (assistant→user→response).
            # These messages are ephemeral — not persisted to session.
            history = list(history)
            history.append({"role": "assistant", "content": "spawn subagent 之后我需要干什么？"})
            history.append({"role": "user", "content": f"Subagent 返回了结果。\n\n{msg.content.strip()}\n\n记住原始任务目标，所有决策围绕最终交付。\n\n请检查 Subagent 状态轮数、检查 team_board.md、处理/更新最新任务状态，有必要的话调整任务、添加新的 subagent、或 cancel 不需要的 subagent。\n\n请继续按计划推进。"})
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
        )
        final_content, _, all_msgs, stop_reason, _, initial_msg_count = await self._loop._run_agent_loop(messages, on_stream=on_stream, on_stream_end=on_stream_end, on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end, session=session, channel=effective_channel, chat_id=chat_id, message_id=msg.metadata.get("message_id"), metadata=msg.metadata, session_key=key, pending_queue=pending_queue)
        if is_subagent and self._loop._persist_subagent_followup(session, msg):
            self._loop.sessions.save(session)
        self._loop._append_turn_to_session(session, all_msgs, initial_msg_count if is_subagent else initial_msg_count - 1)
        self._loop.lifecycle.finalize(session)
        content = final_content or "Background task completed."
        buttons: list = []
        outbound_metadata: dict[str, Any] = {}
        if effective_channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        return OutboundMessage(channel=effective_channel, chat_id=chat_id, content=content, buttons=buttons, metadata=outbound_metadata)


class UserMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, session_key, on_progress, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None):

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

        # Stage 0a: Stale message guard — skip only if same message_id was
        # already processed more than N hours ago. Recent re-dispatches
        # (e.g. from card action callbacks) are allowed through.
        msg_id = msg.metadata.get("message_id", "") or ""
        if _has_stale_duplicate(session, msg_id):
            logger.info("Stale re-dispatch detected for session {} (msg='{}...'), skipping", key, msg.content[:40])
            return None

        # Stage 1: session preparation (checkpoint restore & history loading)
        session, pending, history, channel, chat_id, key = self._prepare_session(msg, session_key)

        # Stage 1a: proactive check → inject two-message pattern for API-compliant delivery
        if msg.metadata.get("proactive_check"):
            history = list(history)
            history.append({"role": "assistant", "content": "spawn subagent 之后我需要干什么？"})
            history.append({"role": "user", "content": msg.content})
            msg = dataclasses.replace(msg, content="")

        # Reset iteration counter — each new turn starts at 0
        self._loop._current_iteration = 0

        # Stage 1.5: compression check — if formatted history exceeds trigger, compress
        from nanobot.utils.helpers import estimate_message_tokens
        _hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        _compress_happened = False
        if _hist_tokens > self._loop._compress_trigger_tokens:
            from nanobot.agent.compress import (
                apply_compress_event, compress_session, MIN_KEEP_TURNS,
            )

            history, event = await compress_session(
                session, history,
                limit=self._loop._history_token_limit, min_keep_turns=MIN_KEEP_TURNS,
            )
            apply_compress_event(session, event, db=self._loop._db)
            _compress_happened = True

        # Stage 1.5b: assess_me triggers — interval + compression
        await self._maybe_assess(session, history, compress_triggered=_compress_happened)

        # Stage 2: tool context
        self._loop._set_tool_context(msg.channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        self._maybe_start_message_tool()

        # Stage 3: build initial messages
        initial_messages, pending_ask_id = self._build_initial_messages(msg, history, pending, session)

        # Stage 4: callbacks
        on_progress_final = on_progress or self._make_bus_progress_callback(msg)
        on_retry_wait = self._make_retry_wait_callback(msg, on_progress_final)

        # Stage 5: persist user message before loop runs
        user_persisted_early = False
        if not msg.ephemeral:
            user_persisted_early = self._persist_user_message_early(session, msg, pending_ask_id)

        # Stage 6: run agent loop
        final_content, _, all_msgs, stop_reason, had_injections, initial_msg_count = await self._loop._run_agent_loop(
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

        # Stage 7: finalize — save, file cap, recovery clear, background schedule
        if msg.ephemeral:
            # Ephemeral messages (e.g. heartbeat) skip history persistence,
            # but still clear any runtime checkpoint the loop may have set.
            self._loop.lifecycle.finalize_ephemeral(session)
        else:
            await self._finalize_turn(session, all_msgs, initial_msg_count, user_persisted_early, final_content)

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
        from nanobot.utils.helpers import estimate_message_tokens

        # Format full history (no budget-based truncation)
        history = session.format_history(include_timestamps=True, timezone=self._loop.context.timezone)

        # Log what format_history actually returned
        hist_tokens = sum(estimate_message_tokens(m) for m in history) if history else 0
        hist_turns = sum(1 for m in history if m.get("role") == "assistant")
        logger.info(
            "HISTORY_DBG: key={}, history_msgs={}, history_turns={}, history_tokens={}",
            key, len(history), hist_turns, hist_tokens,
        )

        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        return session, pending, history, channel, chat_id, key

    async def _maybe_assess(self, session, history, compress_triggered: bool = False) -> None:
        """Check assess_me trigger conditions and inject if needed."""
        from nanobot.agent.loop_constants import _DEFAULT_ASSESS_INTERVAL
        from nanobot.agent.assess_me import assess_me, build_assessment_message

        trigger = False

        # (1) Compression auto-trigger
        if compress_triggered:
            trigger = True

        # (2) Interval trigger — count LLM turns, not user messages
        # Dense tool-call sequences need periodic direction checks too
        if not trigger:
            assistant_count = sum(
                1 for m in session.messages
                if m.get("role") == "assistant"
            )
            if assistant_count > 0 and assistant_count % _DEFAULT_ASSESS_INTERVAL == 0:
                trigger = True

        if not trigger:
            return

        try:
            result = await assess_me(history)
        except Exception as e:
            logger.warning("assess_me LLM call failed: {}", e)
            return

        if result:
            history.append(build_assessment_message(result))
            logger.info(
                "assess_me triggered (compress={}, session={}, history={} msgs)",
                compress_triggered, session.key, len(history),
            )
        else:
            logger.info("assess_me returned empty — LLM had no conclusion, skipping injection")

    async def _dispatch_command(self, msg, session, key):
        """Run command dispatch, return result if handled."""
        from nanobot.command import CommandContext
        ctx = CommandContext(msg=msg, session=session, key=key, raw=msg.content.strip(), loop=self._loop)
        # Priority commands (e.g. /stop, /restart) are checked before the
        # dispatch lock in the bus loop path; for direct/proxy messages we
        # must check them here too since they aren't in the regular dispatch.
        # When a priority handler returns None (e.g. re-dispatched /stop with
        # _stop_redispatch), DO NOT fall through to dispatch() — that would
        # hit cmd_unknown and return "Unknown command" instead of letting the
        # LLM process it (e.g. to update TREE.md).
        if self._loop.commands.is_priority(ctx.raw):
            return await self._loop.commands.dispatch_priority(ctx)
        result = await self._loop.commands.dispatch_priority(ctx)
        if result:
            return result
        return await self._loop.commands.dispatch(ctx)

    def _maybe_start_message_tool(self):
        """Notify message tool that a turn has started."""
        if message_tool := self._loop.tools.get("message_tool"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

    def _build_initial_messages(self, msg, history, pending, session):
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
        )
        return initial_messages, None

    def _make_bus_progress_callback(self, msg):
        async def _bus_progress(content, *, tool_hint=False, tool_events=None):
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            if tool_events:
                meta["_tool_events"] = tool_events
            await self._loop.bus.publish_outbound(OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta))
        return _bus_progress

    def _make_retry_wait_callback(self, msg, on_progress=None):
        async def _on_retry_wait(content):
            # Internal retry categories are meaningless to the user — skip them.
            if content in {"empty_response", "length_recovery"}:
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

    async def _finalize_turn(self, session, all_msgs, initial_msgs_count, user_persisted_early, final_content):
        """Save turn, enforce file cap, clear recovery state."""
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE
        # skip: system prompt (1) + retained_history + user message if already in session
        # initial_msgs_count = 1 + len(retained_history) + 1
        save_skip = initial_msgs_count if user_persisted_early else initial_msgs_count - 1
        self._loop._append_turn_to_session(session, all_msgs, save_skip)

        # Lifecycle: cap, clear checkpoints, save
        self._loop.lifecycle.finalize(session)

        # .pt save: every N turns, using session assistant count (persists across restarts)
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        if assistant_count > 0 and assistant_count % self._loop._pt_save_interval == 0:
            MemoryExtractor.save_prompt_snapshot(all_msgs, self._loop.prompts_dir, session.key)

    def _build_outbound(self, msg, final_content, stop_reason, all_msgs, had_injections, on_stream):
        """Format the final OutboundMessage for the user."""
        if not msg.ephemeral and (mt := self._loop.tools.get("message_tool")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None
        if final_content is None:
            final_content = ""
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        buttons: list = []
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_content, metadata=meta, buttons=buttons)
