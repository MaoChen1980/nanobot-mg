"""Tests for _SubagentHook — thinking tool context injection."""

from __future__ import annotations

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.subagent import _SubagentHook
from nanobot.agent.tools.assess_me import AssessMeTool
from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
from nanobot.agent.tools.registry import ToolRegistry


_SAMPLE_MESSAGES = [
    {"role": "user", "content": "I get a TypeError"},
    {"role": "assistant", "content": "Let me check"},
    {"role": "tool", "name": "read", "content": "some result"},
]


def _make_hook_context(messages: list | None = None) -> AgentHookContext:
    return AgentHookContext(
        iteration=1,
        messages=messages or [],
    )


class TestSubagentHookContextInjection:

    @pytest.mark.asyncio
    async def test_injects_messages_into_assess_me_tool(self) -> None:
        registry = ToolRegistry()
        assess_me = AssessMeTool()
        registry.register(assess_me)
        registry.register(DebugRootCauseTool())

        hook = _SubagentHook(task_id="test-1", tools=registry)
        ctx = _make_hook_context(messages=_SAMPLE_MESSAGES)

        await hook.before_execute_tools(ctx)

        assert assess_me._messages.get() == _SAMPLE_MESSAGES

    @pytest.mark.asyncio
    async def test_injects_messages_into_debug_root_cause_tool(self) -> None:
        registry = ToolRegistry()
        drc = DebugRootCauseTool()
        registry.register(drc)
        registry.register(AssessMeTool())

        hook = _SubagentHook(task_id="test-2", tools=registry)
        ctx = _make_hook_context(messages=_SAMPLE_MESSAGES)

        await hook.before_execute_tools(ctx)

        assert drc._messages.get() == _SAMPLE_MESSAGES

    @pytest.mark.asyncio
    async def test_does_not_inject_when_tools_is_none(self) -> None:
        hook = _SubagentHook(task_id="test-3")
        ctx = _make_hook_context(messages=_SAMPLE_MESSAGES)

        await hook.before_execute_tools(ctx)

    @pytest.mark.asyncio
    async def test_does_not_inject_when_tool_not_in_registry(self) -> None:
        registry = ToolRegistry()
        registry.register(AssessMeTool())

        hook = _SubagentHook(task_id="test-4", tools=registry)
        ctx = _make_hook_context(messages=_SAMPLE_MESSAGES)

        await hook.before_execute_tools(ctx)

    @pytest.mark.asyncio
    async def test_injects_empty_messages_when_messages_is_empty(self) -> None:
        registry = ToolRegistry()
        assess_me = AssessMeTool()
        registry.register(assess_me)

        hook = _SubagentHook(task_id="test-5", tools=registry)
        ctx = _make_hook_context(messages=[])

        await hook.before_execute_tools(ctx)

        assert assess_me._messages.get() == []
