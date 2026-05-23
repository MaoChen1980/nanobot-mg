"""Message handler classes for AgentLoop."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage, OutboundMessage

from nanobot.agent.context import ContextState
from nanobot.bus.events import OutboundMessage
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from nanobot.agent.tools.message import MessageTool


def _has_recent_user_response(session, content):
    """Check if session already has a user message matching *content* with an assistant response."""
    for i in range(len(session.messages) - 1, -1, -1):
        if session.messages[i].get("role") == "assistant":
            continue
        if session.messages[i].get("role") == "user":
            stored = session.messages[i].get("content", "")
            if stored.strip() == content.strip():
                has_assistant = any(
                    m.get("role") == "assistant" and m.get("content")
                    for m in session.messages[i:]
                )
                return has_assistant
        break
    return False


class SystemMessageHandler:
    def __init__(self, loop):
        self._loop = loop

    async def handle(self, msg, on_stream, on_stream_end, on_reasoning=None, on_reasoning_end=None, pending_queue=None):
        from nanobot.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self._loop.sessions.get_or_create(key)
        if self._loop._recovery.restore_and_clear_checkpoint(session):
            self._loop.sessions.save(session)
        if self._loop._recovery.restore_pending_user_turn(session):
            self._loop.sessions.save(session)
        pending = None
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and self._loop._persist_subagent_followup(session, msg):
            self._loop.sessions.save(session)
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
        history = session.get_history(max_turns=200, max_tokens=max(128, adjusted), include_timestamps=True, timezone=self._loop.context.timezone)
        current_role = "assistant" if is_subagent else "user"
        cs = ContextState(
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
        )
        final_content, _, all_msgs, stop_reason, _ = await self._loop._run_agent_loop(messages, on_stream=on_stream, on_stream_end=on_stream_end, on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end, session=session, channel=effective_channel, chat_id=chat_id, message_id=msg.metadata.get("message_id"), metadata=msg.metadata, session_key=key, pending_queue=pending_queue)
        msgs_count = len(messages)
        self._loop._append_turn_to_session(session, all_msgs, msgs_count if is_subagent else msgs_count - 1)
        session.enforce_file_cap()
        self._loop._recovery.clear_runtime_checkpoint(session)
        self._loop.sessions.save(session)
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
                labels.append(pa.name)

            # Inform LLM what was received, no content/vision extraction
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

        # Stage 1: session preparation
        session, pending, history, channel, chat_id, key = self._prepare_session(msg, session_key)

        # Fix 3: Re-dispatch guard — skip if session already has this exact message
        # with a matching assistant response (means prior dispatch already completed).
        if _has_recent_user_response(session, msg.content):
            logger.info("Re-dispatch detected for session {} (msg='{}...'), skipping", key, msg.content[:40])
            return None

        # Fix 1: Reset iteration counter — each new turn starts at 0
        self._loop._current_iteration = 0

        # Stage 2: tool context
        self._loop._set_tool_context(msg.channel, chat_id, msg.metadata.get("message_id"), msg.metadata, session_key=key)
        self._maybe_start_message_tool()

        # Stage 3: build initial messages
        initial_messages, pending_ask_id = self._build_initial_messages(msg, history, pending, session)
        initial_msgs_count = len(initial_messages)

        # Stage 4: callbacks
        on_progress_final = on_progress or self._make_bus_progress_callback(msg)
        on_retry_wait = self._make_retry_wait_callback(msg)

        # Stage 5: persist user message before loop runs
        user_persisted_early = False
        if not msg.ephemeral:
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

        # Stage 7: finalize — save, file cap, recovery clear, background schedule
        if msg.ephemeral:
            # Ephemeral messages (e.g. heartbeat) skip history persistence,
            # but still clear any runtime checkpoint the loop may have set.
            self._loop._recovery.clear_runtime_checkpoint(session)
            self._loop.sessions.save(session)
        else:
            self._finalize_turn(session, all_msgs, initial_msgs_count, user_persisted_early, final_content)

        # Stage 8: build outbound response
        return self._build_outbound(msg, final_content, stop_reason, all_msgs, had_injections, on_stream)

    def _prepare_session(self, msg, session_key):
        """Restore checkpoints, return session + derived context."""
        key = session_key or msg.session_key
        session = self._loop.sessions.get_or_create(key)
        self._loop._recovery.restore_and_clear_checkpoint(session)
        self._loop._recovery.restore_pending_user_turn(session)
        pending = None
        from nanobot.utils.helpers import estimate_message_tokens
        raw_budget = self._loop._compute_history_budget()
        tool_defs = self._loop.tools.get_definitions()
        sys_prompt = self._loop.context.build_system_prompt(channel=msg.channel, tool_definitions=tool_defs)
        sys_tokens = estimate_message_tokens({"role": "system", "content": sys_prompt})
        adjusted = raw_budget - sys_tokens
        if adjusted < 1024:
            adjusted = raw_budget
        history = session.get_history(max_turns=200, max_tokens=max(128, adjusted), include_timestamps=True, timezone=self._loop.context.timezone)

        # Fix 2: If history is empty but session has messages, it means the token
        # budget dropped everything. Log warning and fall back to unfiltered history.
        if not history and session.messages:
            logger.warning(
                "get_history returned empty for session {} ({} msgs, budget={}), falling back",
                key, len(session.messages), adjusted,
            )
            history = session.get_history(max_turns=200, max_tokens=0, include_timestamps=True, timezone=self._loop.context.timezone)

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

    def _build_initial_messages(self, msg, history, pending, session):
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
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if not pending_ask_id and (has_text or media_paths):
            extra = {"media": list(media_paths)} if media_paths else {}
            text = msg.content if isinstance(msg.content, str) else ""
            session.add_message("user", text, timestamp=msg.timestamp.isoformat(), **extra)
            self._loop._recovery.mark_pending_user_turn(session)
            return True
        return False

    def _finalize_turn(self, session, all_msgs, initial_msgs_count, user_persisted_early, final_content):
        """Save turn, enforce file cap, clear recovery state."""
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE
        # skip: system prompt (1) + retained_history + user message if already in session
        # initial_msgs_count = 1 + len(retained_history) + 1
        save_skip = initial_msgs_count if user_persisted_early else initial_msgs_count - 1
        self._loop._append_turn_to_session(session, all_msgs, save_skip)

        # Turn-based archive: when session exceeds N turns, archive oldest M turns to history
        max_turns = session.metadata.get("max_turns", 200)
        trim_batch = session.metadata.get("trim_batch", 50)
        trimmed = session.trim_old_turns(max_turns, trim_batch)
        if trimmed:
            archived = self._loop.context.memory.condense_session_to_history(trimmed)
            logger.info("_finalize_turn: archived {} oldest turns (N={}, M={})", archived, max_turns, trim_batch)

        session.enforce_file_cap()
        self._loop._recovery.clear_pending_user_turn(session)
        self._loop._recovery.clear_runtime_checkpoint(session)
        self._loop.sessions.save(session)

        # .pt save: every N turns (50, 100, 150…), save a snapshot
        turn_count = self._loop._pt_counters.get(session.key, 0) + 1
        self._loop._pt_counters[session.key] = turn_count
        if turn_count % self._loop._pt_save_interval == 0:
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
