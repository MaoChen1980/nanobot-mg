"""Message handler classes for AgentLoop."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage, OutboundMessage

from nanobot.agent.context import ContextState
from nanobot.bus.events import OutboundMessage
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from nanobot.agent.tools.message import MessageTool


class SystemMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, on_stream, on_stream_end, pending_queue):
        from nanobot.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self._loop.sessions.get_or_create(key)
        if self._loop._recovery.restore_runtime_checkpoint(session):
            self._loop.sessions.save(session)
        if self._loop._recovery.restore_pending_user_turn(session):
            self._loop.sessions.save(session)
        session, pending = self._loop.auto_compact.prepare_session(session, key)
        await self._loop.consolidator.maybe_consolidate_by_tokens(session, session_summary=pending)
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and self._loop._persist_subagent_followup(session, msg):
            self._loop.sessions.save(session)
        # For "system" channel (subagent), use channel extracted from chat_id (e.g. "slack").
        # For all other channels (cron, proxy, direct), use msg.channel directly.
        effective_channel = channel if msg.channel == "system" else msg.channel
        self._loop._set_tool_context(effective_channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        history = session.get_history(max_tokens=self._loop._replay_token_budget(), include_timestamps=True, timezone=self._loop.context.timezone)
        current_role = "assistant" if is_subagent else "user"
        cs = ContextState(
            session_summary=pending,
            tool_definitions=self._loop.tools.get_definitions(),
            current_iteration=self._loop._current_iteration,
            max_iterations=self._loop.max_iterations,
        )
        messages = self._loop.context.build_messages(
            history=history,
            current_message="" if is_subagent else msg.content,
            channel=effective_channel,
            chat_id=chat_id,
            current_role=current_role,
            context_state=cs,
            message_timestamp=msg.timestamp.isoformat() if hasattr(msg, 'timestamp') else None,
        )
        final_content, _, all_msgs, stop_reason, _ = await self._loop._run_agent_loop(messages, session=session, channel=effective_channel, chat_id=chat_id, message_id=msg.metadata.get("message_id"), metadata=msg.metadata, session_key=key, pending_queue=pending_queue)
        self._loop._record_turn(session, all_msgs, 1 + len(history))
        session.enforce_file_cap(on_archive=self._loop.context.memory.raw_archive)
        self._loop._recovery.clear_runtime_checkpoint(session)
        self._loop.sessions.save(session)
        self._loop._schedule_background(self._loop.consolidator.maybe_consolidate_by_tokens(session))
        options = ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else []
        content, buttons = ask_user_outbound(final_content or "Background task completed.", options, effective_channel)
        outbound_metadata: dict[str, Any] = {}
        if effective_channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        return OutboundMessage(channel=effective_channel, chat_id=chat_id, content=content, buttons=buttons, metadata=outbound_metadata)


class UserMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, session_key, on_progress, on_stream, on_stream_end, pending_queue):
        from nanobot.utils.document import extract_documents
        from nanobot.agent.tools.ask import pending_ask_user_id, ask_user_tool_result_messages

        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = dataclasses.replace(msg, content=new_content, media=image_only)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Stage 1: session preparation
        session, pending, history, channel, chat_id, key = self._prepare_session(msg, session_key)

        # Stage 2: command dispatch (early return)
        if result := await self._dispatch_command(msg, session, key):
            return result

        # Stage 3: consolidation + tool context
        await self._loop.consolidator.maybe_consolidate_by_tokens(session, session_summary=pending)
        self._loop._set_tool_context(msg.channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        self._maybe_start_message_tool()

        # Stage 4: build initial messages
        initial_messages, pending_ask_id = self._build_initial_messages(msg, history, pending)

        # Stage 5: callbacks
        on_progress_final = on_progress or self._make_bus_progress_callback(msg)
        on_retry_wait = self._make_retry_wait_callback(msg)

        # Stage 6: persist user message before loop runs
        user_persisted_early = self._persist_user_message_early(session, msg, pending_ask_id)

        # Stage 7: run agent loop
        final_content, _, all_msgs, stop_reason, had_injections = await self._loop._run_agent_loop(
            initial_messages,
            on_progress=on_progress_final,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_retry_wait=on_retry_wait,
            session=session,
            channel=msg.channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            pending_queue=pending_queue,
        )

        # Stage 8: finalize — save, file cap, recovery clear, background schedule
        self._finalize_turn(session, all_msgs, history, user_persisted_early, final_content)

        # Stage 9: build outbound response
        return self._build_outbound(msg, final_content, stop_reason, all_msgs, had_injections, on_stream)

    def _prepare_session(self, msg, session_key):
        """Restore checkpoints, return session + derived context."""
        key = session_key or msg.session_key
        session = self._loop.sessions.get_or_create(key)
        if self._loop._recovery.restore_runtime_checkpoint(session):
            self._loop.sessions.save(session)
        if self._loop._recovery.restore_pending_user_turn(session):
            self._loop.sessions.save(session)
        session, pending = self._loop.auto_compact.prepare_session(session, key)
        history = session.get_history(max_tokens=self._loop._replay_token_budget(), include_timestamps=True, timezone=self._loop.context.timezone)
        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        return session, pending, history, channel, chat_id, key

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

    def _build_initial_messages(self, msg, history, pending):
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
                session_summary=pending,
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
                message_timestamp=msg.timestamp.isoformat(),
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
        """Persist the user message before the loop runs, enabling crash recovery."""
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if not pending_ask_id and (has_text or media_paths):
            extra = {"media": list(media_paths)} if media_paths else {}
            text = msg.content if isinstance(msg.content, str) else ""
            session.add_message("user", text, timestamp=msg.timestamp.isoformat(), **extra)
            self._loop._recovery.mark_pending_user_turn(session)
            self._loop.sessions.save(session)
            return True
        return False

    def _finalize_turn(self, session, all_msgs, history, user_persisted_early, final_content):
        """Save turn, enforce file cap, clear recovery state, schedule consolidation."""
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        self._loop._record_turn(session, all_msgs, save_skip)
        session.enforce_file_cap(on_archive=self._loop.context.memory.raw_archive)
        self._loop._recovery.clear_pending_user_turn(session)
        self._loop._recovery.clear_runtime_checkpoint(session)
        self._loop.sessions.save(session)
        self._loop._schedule_background(self._loop.consolidator.maybe_consolidate_by_tokens(session))

    def _build_outbound(self, msg, final_content, stop_reason, all_msgs, had_injections, on_stream):
        """Format the final OutboundMessage for the user."""
        from nanobot.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        if (mt := self._loop.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None
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
