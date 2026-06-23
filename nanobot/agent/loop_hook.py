"""Loop hook — streaming, iteration, and tool execution hooks for AgentLoop."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.utils.progress_events import (
    build_tool_event_finish_payloads,
    build_tool_event_start_payload,
    on_progress_accepts_tool_events,
    process_tool_events_and_progress,
)

# Strip tool_summary markers entirely (tag + content) from user-facing output.
# The summary replaces the tool result in session history, not the assistant's
# visible response.  Same pattern as _SUMMARY_RE in loop.py.
_USER_TOOL_SUMMARY_RE = re.compile(
    r'\[tool_summary:([^\]]+)\](.*?)\[/tool_summary\]',
    re.DOTALL,
)

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.providers.base import ToolCallRequest

class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_reasoning: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_end: Callable[..., Awaitable[None]] | None = None,
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
        self._on_reasoning = on_reasoning
        self._on_reasoning_end = on_reasoning_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._metadata = metadata or {}
        self._session_key = session_key
        self._stream_buf = ""
        self._reasoning_buf = ""
        self._had_content = False
        self._observe_think = observe_think
        self._observe_tool = observe_tool

    def wants_streaming(self) -> bool:
        return True

    @staticmethod
    def _extract_think_content(text: str) -> str:
        """Extract content inside <think> tags."""
        import re
        matches = re.findall(r'<think>(.*?)</think>', text, re.DOTALL)
        return "\n".join(matches)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from nanobot.agent.loop_utils import strip_think

        clean_buf = _USER_TOOL_SUMMARY_RE.sub("", self._stream_buf)
        if len(clean_buf) != len(self._stream_buf):
            matches = _USER_TOOL_SUMMARY_RE.findall(self._stream_buf)
            marker_info = "; ".join(f"[{cid}]{summary[:60]}[/{cid}]" for cid, summary in matches)
            logger.info("TOOL_SUMMARY: stripped markers from streaming (delta={} chars, markers: {})", len(self._stream_buf) - len(clean_buf), marker_info)
        prev_clean = strip_think(clean_buf) or ""
        prev_think = self._extract_think_content(self._stream_buf)
        self._stream_buf += delta
        new_clean_buf = _USER_TOOL_SUMMARY_RE.sub("", self._stream_buf)
        new_clean = strip_think(new_clean_buf) or ""
        new_think = self._extract_think_content(self._stream_buf)

        # Forward incremental non-think text
        text_incremental = new_clean[len(prev_clean):]
        if text_incremental and self._on_stream:
            self._had_content = True
            await self._on_stream(text_incremental)

        # Buffer think/reasoning — only flush if no non-think content appears
        think_incremental = new_think[len(prev_think):]
        if think_incremental:
            self._reasoning_buf += think_incremental

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        # Flush buffered reasoning only when no non-think content was streamed
        if self._reasoning_buf and self._on_reasoning and not self._had_content:
            await self._on_reasoning(self._reasoning_buf)
        self._reasoning_buf = ""
        if self._on_reasoning_end:
            await self._on_reasoning_end()
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration
        self._had_content = False
        self._reasoning_buf = ""

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
        await process_tool_events_and_progress(
            self._on_progress,
            effective_content,
            tool_hint=effective_tool_hint,
            tool_events=effective_tool_events,
        )

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration

        # Send LLM thinking via reasoning stream when /think is on
        if self._observe_think:
            reasoning = (context.response.reasoning_content
                         if context.response and context.response.reasoning_content
                         else None)
            if reasoning and self._on_reasoning:
                # Only send reasoning when no non-think content exists
                content = (context.response.content or "") if context.response else ""
                clean = self._loop._strip_think(content)
                if not clean:
                    await self._on_reasoning(reasoning)
            else:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought and self._on_reasoning:
                    await self._on_reasoning(thought)

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
        # handles the tool-call path).  Only send when response has no non-think content.
        if self._observe_think and not context.tool_calls:
            reasoning = (context.response.reasoning_content
                         if context.response and context.response.reasoning_content
                         else None)
            if reasoning:
                content = (context.response.content or "") if context.response else ""
                clean = self._loop._strip_think(content)
                if not clean and self._on_progress is not None:
                    await self._on_progress(reasoning)

        # Log tool execution results
        if context.tool_events:
            for te in context.tool_events:
                detail = te.get("detail", "")
                logger.info("Tool result: {} — {} {}", te["name"], te["status"], detail)
        elif context.tool_calls and not context.tool_events:
            logger.warning("Tool calls made but no events recorded ({} call(s))", len(context.tool_calls))

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
        if content is None:
            return None
        # NOTE: do NOT strip [tool_summary] markers here — they must survive in
        # result.messages so _append_turn_to_session can collect and replace
        # tool results.  Streaming output is cleaned by on_stream(), and the
        # non-streaming final_content is cleaned in _build_outbound().
        return self._loop._strip_think(content)

    def before_llm_call(
        self, context: AgentHookContext, messages: list[dict]
    ) -> list[dict]:
        return messages

    async def after_turn(self) -> None:
        """Called after a full user-message turn completes."""
        pass

    def filter_tool_calls(
        self, context: AgentHookContext, tool_calls: list[ToolCallRequest]
    ) -> list[ToolCallRequest]:
        return tool_calls

