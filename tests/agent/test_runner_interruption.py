"""Tests for mid-turn interruption handling in the runner.

Covers the ``was_interrupted`` block in ``runner.py`` — closing assistant
generation, tool_calls stripping, and user injection appending.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.config.schema import AgentDefaults
from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


# ── Helpers ──────────────────────────────────────────────────────────────────


class _SeqInjectionCallback:
    """Returns pre-defined injection results in sequence, then empty."""
    def __init__(self, *results):
        self._results = list(results)
        self.call_count = 0

    async def __call__(self, **kwargs):
        if self.call_count < len(self._results):
            r = self._results[self.call_count]
            self.call_count += 1
            return r
        self.call_count += 1
        return []


def _make_provider(
    tool_calls: list[ToolCallRequest],
    first_content: str | None = None,
    final_content: str = "done",
):
    """Mock provider: first call returns *tool_calls*, subsequent calls return
    *final_content* with empty tool_calls."""
    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=first_content,
                tool_calls=list(tool_calls),
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        return LLMResponse(
            content=final_content,
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")
    return provider


def _make_tools(side_effect=None):
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=side_effect or (lambda name, args, **kw: "ok"))
    return tools


def _has_role(msgs: list[dict], role: str) -> int:
    return sum(1 for m in msgs if m.get("role") == role)


# ── Injection scenarios ──────────────────────────────────────────────────────


class TestInterruptionInjection:
    """User sends a new message during tool execution."""

    @pytest.mark.asyncio
    async def test_all_tools_then_injection(self):
        """1a: All tools complete → closing states completion + user directive.

        Sequence (tool_b completes → injection checked → was_interrupted):
          assistant[tc1, tc2] → tool(tc1) → tool(tc2)
          → assistant(closing: "已完成。用户发送了新消息...")
          → user(injection) → assistant(done)
        """
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider([
            ToolCallRequest(id="tc1", name="tool_a", arguments={}),
            ToolCallRequest(id="tc2", name="tool_b", arguments={}),
        ])

        # First check (after tool_a): empty → Second check (after tool_b): injection
        callback = _SeqInjectionCallback(
            [],
            [{"role": "user", "content": "new instruction"}],
        )

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            injection_callback=callback,
        ))

        # Structural checks
        assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
        user_msgs = [m for m in result.messages if m.get("role") == "user"]

        # 3 assistant messages: original tc, closing, final
        assert len(assistant_msgs) == 3
        # 2 user messages: initial + injection
        assert len(user_msgs) == 2

        # Closing assistant (second assistant msg) mentions completion + directive
        closing = assistant_msgs[1]
        assert "已完成" in closing["content"]
        assert "用户发送了新消息" in closing["content"]

        # Injection follows the closing assistant
        assert user_msgs[-1]["content"] == "new instruction"

        # No BYPASSED markers
        assert not any("[BYPASSED]" in str(m) for m in result.messages)

        # Tool results for both tools
        assert _has_role(result.messages, "tool") == 2

    @pytest.mark.asyncio
    async def test_partial_execution_then_injection(self):
        """1b: 1/3 tools done, injection arrives → strip + closing + injection.

        Sequence:
          assistant[tc1, tc2, tc3] → tool(tc1)
          → injection checked → was_interrupted → strip tc2/tc3 from assistant
          → assistant(closing: "已完成。我打算晚一点再执行。用户发送了新消息...")
          → user(injection) → assistant(done)
        """
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider([
            ToolCallRequest(id="tc1", name="tool_a", arguments={}),
            ToolCallRequest(id="tc2", name="tool_b", arguments={}),
            ToolCallRequest(id="tc3", name="tool_c", arguments={}),
        ])

        # Injection arrives after first tool
        callback = _SeqInjectionCallback(
            [{"role": "user", "content": "change plan"}],
        )

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            injection_callback=callback,
        ))

        assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]

        # The first assistant should have only 1 tool_call (stripped)
        first_asst = assistant_msgs[0]
        assert len(first_asst.get("tool_calls", [])) == 1
        assert first_asst["tool_calls"][0]["function"]["name"] == "tool_a"

        # Closing assistant mentions pending items
        closing = assistant_msgs[1]
        assert "已完成" in closing["content"]
        assert "我打算晚一点再执行" in closing["content"]
        assert "用户发送了新消息" in closing["content"]

        # Only 1 tool result (tool_a ran, tool_b/tool_c stripped)
        assert _has_role(result.messages, "tool") == 1

        # No BYPASSED markers
        assert not any("[BYPASSED]" in str(m) for m in result.messages)

    @pytest.mark.asyncio
    async def test_injection_preserves_result_with_text_content(self):
        """2b: Partial execution + original assistant has text → text prepended."""
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider(
            [ToolCallRequest(id="tc1", name="tool_a", arguments={})],
            first_content="我正在处理",
        )

        callback = _SeqInjectionCallback(
            [{"role": "user", "content": "quick question"}],
        )

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            injection_callback=callback,
        ))

        assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
        closing = assistant_msgs[1]

        # Original text "我正在处理" should be preserved at start of closing
        assert closing["content"].startswith("我正在处理")
        assert "已完成" in closing["content"]
        assert "用户发送了新消息" in closing["content"]


# ── Tool Error scenarios ─────────────────────────────────────────────────────


class TestInterruptionToolError:
    """A tool raises RuntimeError during execution."""

    @pytest.mark.asyncio
    async def test_tool_error_mid_chain(self):
        """3b: 2 tools, error on second → closing states success + failure."""
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider([
            ToolCallRequest(id="tc1", name="tool_a", arguments={}),
            ToolCallRequest(id="tc2", name="tool_b", arguments={}),
        ])

        async def _tool_execute(name, args, **kw):
            if name == "tool_b":
                raise RuntimeError("something went wrong")
            return "ok"

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(side_effect=_tool_execute),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            fail_on_tool_error=True,
        ))

        # Closing assistant should mention failure
        error_msg = next(
            (m for m in result.messages
             if m.get("role") == "assistant" and "失败" in str(m.get("content", ""))),
            None,
        )
        assert error_msg is not None, "Expected closing assistant with '失败'"
        assert "tool_b" in error_msg["content"] or "失败" in error_msg["content"]

        # Both tool results should be in messages
        assert _has_role(result.messages, "tool") == 2

    @pytest.mark.asyncio
    async def test_tool_error_first_tool(self):
        """Error on the very first tool → closing says it failed."""
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider([
            ToolCallRequest(id="tc1", name="tool_a", arguments={}),
        ])

        async def _fail_first(name, args, **kw):
            raise RuntimeError("first tool failed")

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(side_effect=_fail_first),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            fail_on_tool_error=True,
        ))

        error_msg = next(
            (m for m in result.messages
             if m.get("role") == "assistant" and "失败" in str(m.get("content", ""))),
            None,
        )
        assert error_msg is not None

        # Tool result (even though failed) should be present
        assert _has_role(result.messages, "tool") == 1


# ── Normal / no-interruption scenarios ───────────────────────────────────────


class TestInterruptionNormal:
    """No interruption — happy path still works correctly."""

    @pytest.mark.asyncio
    async def test_no_interruption_all_tools_ok(self):
        """All tools execute → normal final response, no closing assistant."""
        from nanobot.agent.runner import AgentRunSpec, AgentRunner

        provider = _make_provider([
            ToolCallRequest(id="tc1", name="tool_a", arguments={}),
            ToolCallRequest(id="tc2", name="tool_b", arguments={}),
        ])

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do stuff"}],
            tools=_make_tools(),
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

        # Normal completion: 2 LLM calls (tool_calls → final answer)
        assert result.final_content == "done"
        assert result.stop_reason == "completed"

        # 2 assistant messages: original tc + final
        assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 2

        # 2 tool results
        assert _has_role(result.messages, "tool") == 2

        # No closing assistant (no "已完成" or "用户发送了新消息")
        for m in assistant_msgs:
            assert "用户发送了新消息" not in m.get("content", "")
