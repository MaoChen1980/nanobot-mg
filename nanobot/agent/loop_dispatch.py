"""Dispatch manager for AgentLoop — handles per-session lock/gate/queue lifecycle."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage, OutboundMessage

from nanobot.bus.events import OutboundMessage
from nanobot.agent.context_vars import _current_inbound


class DispatchManager:
    """Manages the dispatch envelope: lock acquisition, concurrency gate,
    streaming setup, cancellation recovery, and queue draining."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    async def run_dispatch(
        self, msg: InboundMessage, session_key: str, pending: asyncio.Queue,
    ) -> None:
        """Execute dispatch body: process message with streaming, handle cancel/error."""
        _current_inbound.set(msg)
        try:
            try:
                on_stream, on_stream_end, on_reasoning, on_reasoning_end = self._maybe_streaming(msg)
                response = await self._loop._process_message(
                    msg, on_stream=on_stream, on_stream_end=on_stream_end,
                    on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end,
                    pending_queue=pending,
                )
                if response is not None:
                    # Carry session_key in metadata so gateway consumers
                    # can route outbound messages (e.g. subagent results)
                    # to the correct proxy connection.
                    if msg.session_key:
                        response = OutboundMessage(
                            channel=response.channel, chat_id=response.chat_id,
                            content=response.content, reply_to=response.reply_to,
                            media=response.media,
                            metadata={**response.metadata, "_session_key": msg.session_key},
                            buttons=response.buttons,
                        )
                    await self._loop.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self._loop.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                await self._handle_cancellation(msg, session_key)
                raise
            except Exception:
                logger.exception(
                    "Error processing message for session {}", session_key,
                )
                # Clean up checkpoint so next turn starts fresh
                try:
                    key = self._effective_session_key(msg)
                    self._loop.lifecycle.cleanup_on_error(key)
                except Exception as inner:
                    logger.debug("Checkpoint cleanup failed: {}", inner)
                await self._loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))
        finally:
            await self._republish_leftover_messages(session_key, pending)

    def _effective_session_key(self, msg: InboundMessage) -> str:
        return msg.session_key

    def _maybe_streaming(
        self, msg: InboundMessage,
    ) -> tuple[Any | None, Any | None, Any | None, Any | None]:
        """Build streaming callbacks. Always active."""
        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
        stream_segment = 0

        def _current_stream_id() -> str:
            return f"{stream_base_id}:{stream_segment}"

        async def on_stream(delta: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_stream_delta"] = True
            meta["_stream_id"] = _current_stream_id()
            await self._loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=delta,
                metadata=meta,
            ))

        async def on_reasoning(delta: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_reasoning_delta"] = True
            meta["_stream_id"] = _current_stream_id()
            await self._loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=delta,
                metadata=meta,
            ))

        async def on_stream_end(*, resuming: bool = False) -> None:
            nonlocal stream_segment
            meta = dict(msg.metadata or {})
            meta["_stream_end"] = True
            meta["_resuming"] = resuming
            meta["_stream_id"] = _current_stream_id()
            await self._loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="",
                metadata=meta,
            ))
            stream_segment += 1

        async def on_reasoning_end() -> None:
            pass

        return on_stream, on_stream_end, on_reasoning, on_reasoning_end

    async def _handle_cancellation(
        self, msg: InboundMessage, session_key: str,
    ) -> None:
        """Restore partial context when task is cancelled (e.g. /stop)."""
        logger.info("Task cancelled for session {}", session_key)
        try:
            key = self._effective_session_key(msg)
            session = self._loop.sessions.get_or_create(key)

            # Restore checkpoint with [STOPPED BY USER] for pending tools.
            # The /stop message itself will be added by the re-dispatch
            # that the bus loop schedules after cancellation.
            self._loop._recovery.restore_and_clear_checkpoint(
                session,
                pending_tool_content="[STOPPED BY USER]",
            )
            self._loop._recovery.clear_pending_user_turn(session)
            self._loop.sessions.save(session)
            logger.info("Restored partial context for cancelled session {}", key)
        except Exception:
            logger.debug(
                "Could not restore checkpoint for cancelled session {}",
                session_key,
                exc_info=True,
            )

    async def _republish_leftover_messages(
        self, session_key: str, queue: asyncio.Queue,
    ) -> None:
        """Re-publish leftover messages from pending queue to bus."""
        state = self._loop._session_dispatch.pop(session_key, None)
        if state is None:
            return
        queue = state.pending
        leftover = 0
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._loop.bus.publish_inbound(item)
            leftover += 1
        if leftover:
            logger.info(
                "Re-published {} leftover message(s) to bus for session {}",
                leftover, session_key,
            )
