"""Loop hook — streaming, iteration, and tool execution hooks for AgentLoop."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.utils.progress_events import (
    build_tool_event_finish_payloads,
    build_tool_event_start_payload,
    invoke_on_progress,
    on_progress_accepts_tool_events,
)

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop

class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        observe_think: bool = False,
        observe_tool: bool = False,
    ) -> None:
        super().__init__(reraise=True)
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._metadata = metadata or {}
        self._session_key = session_key
        self._stream_buf = ""
        self._observe_think = observe_think
        self._observe_tool = observe_tool

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from nanobot.agent.loop_utils import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean) :]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration

    async def _send_progress(
        self,
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Send progress to the bus via on_progress, respecting observe toggles."""
        if not self._on_progress:
            return
        # Respect /tool toggle: only emit tool hints/events when /tool is on
        effective_tool_hint = tool_hint and self._observe_tool
        effective_tool_events = tool_events if self._observe_tool else None
        # Respect /think toggle for content-only progress
        effective_content = content if self._observe_think or self._observe_tool else ""
        # Skip if nothing to send (e.g. tool hint but /tool is off and no content)
        if not effective_content and not effective_tool_events:
            return
        await invoke_on_progress(
            self._on_progress,
            effective_content,
            tool_hint=effective_tool_hint,
            tool_events=effective_tool_events,
        )

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration

        # Send LLM thinking when /think is on and not streaming
        if self._observe_think and not self._on_stream:
            # Prefer explicit reasoning_content (DeepSeek-R1 etc.)
            reasoning = (context.response.reasoning_content
                         if context.response and context.response.reasoning_content
                         else None)
            if reasoning:
                await self._on_progress(reasoning)
            else:
                # Fall back to text outside think tags as a "preview"
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)

        # Send tool start events when /tool is on
        if self._observe_tool:
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            tool_events = [build_tool_event_start_payload(tc) for tc in context.tool_calls]
            await self._send_progress(tool_hint, tool_hint=True, tool_events=tool_events)

        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(
            self._channel,
            self._chat_id,
            self._message_id,
            self._metadata,
            session_key=self._session_key,
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        # Send LLM reasoning when /think is on and no tool calls (before_execute_tools
        # handles the tool-call path) and not streaming.
        if self._observe_think and not self._on_stream and not context.tool_calls:
            reasoning = (context.response.reasoning_content
                         if context.response and context.response.reasoning_content
                         else None)
            if reasoning:
                await self._on_progress(reasoning)

        # Send tool finish events when /tool is on
        if (
            self._observe_tool
            and context.tool_calls
            and context.tool_events
            and on_progress_accepts_tool_events(self._on_progress)
        ):
            tool_events = build_tool_event_finish_payloads(context)
            if tool_events:
                await self._send_progress("", tool_hint=False, tool_events=tool_events)
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)

