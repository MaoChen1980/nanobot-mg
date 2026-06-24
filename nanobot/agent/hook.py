"""Shared lifecycle hook primitives for agent runs."""

from __future__ import annotations

from dataclasses import field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.utils.compat import dataclass


@dataclass(slots=True)
class AgentRunHookContext:
    """Run-level state snapshot exposed to runner hooks."""

    messages: list[dict[str, Any]]
    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
    exception: BaseException | None = None


@dataclass(slots=True)
class AgentHookContext:
    """Mutable per-iteration state exposed to runner hooks."""

    iteration: int
    messages: list[dict[str, Any]]
    response: LLMResponse | None = None
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    tool_events: list[dict[str, str]] = field(default_factory=list)
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    workspace: Path | None = None
    # Per-iteration aggregate
    duration_sec: float = 0.0  # total tool call duration in this iteration (set by SelfLogHook)


class AgentHook:
    """Minimal lifecycle surface for shared runner customization."""

    def __init__(self, reraise: bool = False) -> None:
        self._reraise = reraise

    def wants_streaming(self) -> bool:
        return False

    async def before_run(self, context: AgentRunHookContext) -> None:
        pass

    async def after_run(self, context: AgentRunHookContext) -> None:
        pass

    async def on_error(self, context: AgentRunHookContext) -> None:
        pass

    async def on_finally(self, context: AgentRunHookContext) -> None:
        pass

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        pass

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        pass

    async def on_reasoning(self, context: AgentHookContext, delta: str) -> None:
        """Called with reasoning/thinking deltas during streaming."""
        pass

    async def on_reasoning_end(self, context: AgentHookContext) -> None:
        """Called when reasoning content streaming ends."""
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        pass

    async def after_turn(self) -> None:
        """Called after a full user-message turn completes.

        Unlike ``after_iteration`` (fires per LLM call), this fires once
        per user message, after all tool-call cycles are done.
        """
        pass

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return content

    def before_llm_call(
        self, context: AgentHookContext, messages: list[dict]
    ) -> list[dict]:
        """Inspect or modify the message list before LLM call.

        Pipeline method: each hook's output feeds the next hook's input.
        Return the (possibly modified) list.
        """
        return messages

    def filter_tool_calls(
        self, context: AgentHookContext, tool_calls: list[ToolCallRequest]
    ) -> list[ToolCallRequest]:
        """Filter or modify tool calls before execution.

        Pipeline method: each hook's output feeds the next hook's input.
        Return the (possibly modified/filtered) list.
        """
        return tool_calls


class CompositeHook(AgentHook):
    """Fan-out hook that delegates to an ordered list of hooks.

    Error isolation: async methods catch and log per-hook exceptions
    so a faulty custom hook cannot crash the agent loop.
    ``finalize_content`` is a pipeline (no isolation — bugs should surface).
    """

    __slots__ = ("_hooks",)

    def __init__(self, hooks: list[AgentHook]) -> None:
        super().__init__()
        self._hooks = list(hooks)

    def wants_streaming(self) -> bool:
        return any(h.wants_streaming() for h in self._hooks)

    async def _for_each_hook_safe(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        for h in self._hooks:
            if getattr(h, "_reraise", False):
                await getattr(h, method_name)(*args, **kwargs)
                continue

            try:
                await getattr(h, method_name)(*args, **kwargs)
            except Exception:
                logger.exception("AgentHook.{} error in {}", method_name, type(h).__name__)

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("before_run", context)

    async def after_run(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("after_run", context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("on_error", context)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("on_finally", context)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_iteration", context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._for_each_hook_safe("on_stream", context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._for_each_hook_safe("on_stream_end", context, resuming=resuming)

    async def on_reasoning(self, context: AgentHookContext, delta: str) -> None:
        await self._for_each_hook_safe("on_reasoning", context, delta)

    async def on_reasoning_end(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("on_reasoning_end", context)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_execute_tools", context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("after_iteration", context)

    async def after_turn(self) -> None:
        await self._for_each_hook_safe("after_turn")

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        for h in self._hooks:
            try:
                content = h.finalize_content(context, content)
            except Exception:
                logger.exception("AgentHook.finalize_content error in {}", type(h).__name__)
        return content

    def before_llm_call(
        self, context: AgentHookContext, messages: list[dict]
    ) -> list[dict]:
        for h in self._hooks:
            try:
                messages = h.before_llm_call(context, messages)
            except Exception:
                logger.exception("AgentHook.before_llm_call error in {}", type(h).__name__)
        return messages

    def filter_tool_calls(
        self, context: AgentHookContext, tool_calls: list[ToolCallRequest]
    ) -> list[ToolCallRequest]:
        for h in self._hooks:
            try:
                tool_calls = h.filter_tool_calls(context, tool_calls)
            except Exception:
                logger.exception("AgentHook.filter_tool_calls error in {}", type(h).__name__)
        return tool_calls


class SDKCaptureHook(AgentHook):
    """Captures tool names, messages, usage, and stop_reason for SDK consumers.

    Used internally by ``Nanobot.run()`` to populate ``RunResult``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tools_used: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.usage: dict[str, int] = {}
        self.stop_reason: str | None = None
        self.error: str | None = None

    async def after_iteration(self, context: AgentHookContext) -> None:
        for tc in context.tool_calls:
            name = tc.name or ""
            if name and name not in self.tools_used:
                self.tools_used.append(name)
        self.messages = context.messages
        self.usage = context.usage
        if context.stop_reason:
            self.stop_reason = context.stop_reason
        if context.error:
            self.error = context.error
