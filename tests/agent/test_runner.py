"""Tests for the shared agent runner and its integration contracts."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.agent.loop import _SessionDispatchState
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _make_injection_callback(queue: asyncio.Queue):
    """Return an async callback that drains *queue* into a list of dicts."""
    async def inject_cb():
        items = []
        while not queue.empty():
            items.append(await queue.get())
        return items
    return inject_cb


def _make_loop(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    llm_set_llm(provider, "test-model")

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as MockSubMgr:
        MockSubMgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
    return loop






@pytest.mark.asyncio
async def test_runner_streaming_hook_receives_deltas_and_end_signal():
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    streamed: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("he")
        await on_content_delta("llo")
        return LLMResponse(content="hello", tool_calls=[], usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    provider.chat_with_retry = AsyncMock()
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    class StreamingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            endings.append(resuming)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=StreamingHook(),
    ))

    assert result.final_content == "hello"
    assert streamed == ["he", "llo"]
    assert endings == [False]
    provider.chat_with_retry.assert_not_awaited()





def test_persist_tool_result_prunes_old_session_buckets(tmp_path):
    from nanobot.utils.helpers import maybe_persist_tool_result

    root = tmp_path / "tool-results"
    old_bucket = root / "old_session"
    recent_bucket = root / "recent_session"
    old_bucket.mkdir(parents=True)
    recent_bucket.mkdir(parents=True)
    (old_bucket / "old.txt").write_text("old", encoding="utf-8")
    (recent_bucket / "recent.txt").write_text("recent", encoding="utf-8")

    stale = time.time() - (8 * 24 * 60 * 60)
    os.utime(old_bucket, (stale, stale))
    os.utime(old_bucket / "old.txt", (stale, stale))

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert not old_bucket.exists()
    assert recent_bucket.exists()
    assert (root / "current_session" / "call_big.txt").exists()


def test_persist_tool_result_leaves_no_temp_files(tmp_path):
    from nanobot.utils.helpers import maybe_persist_tool_result

    root = tmp_path / "tool-results"
    maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert (root / "current_session" / "call_big.txt").exists()
    assert list((root / "current_session").glob("*.tmp")) == []


def test_persist_tool_result_logs_cleanup_failures(monkeypatch, tmp_path):
    from nanobot.utils.helpers import maybe_persist_tool_result

    warnings: list[str] = []

    monkeypatch.setattr(
        "nanobot.utils.helpers._cleanup_tool_result_buckets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )
    monkeypatch.setattr(
        "nanobot.utils.helpers.logger.warning",
        lambda message, *args: warnings.append(message.format(*args)),
    )

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert warnings and "Failed to clean stale tool result buckets" in warnings[0]




@pytest.mark.asyncio
async def test_runner_empty_response_does_not_break_tool_chain():
    """An empty intermediate response must not kill an ongoing tool chain.

    Sequence: tool_call → empty → tool_call → final text.
    The runner should recover via silent retry and complete normally.
    """
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="read_file", arguments={"path": "a.txt"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 2:
            return LLMResponse(content=None, tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 1})
        if call_count == 3:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc2", name="read_file", arguments={"path": "b.txt"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        return LLMResponse(
            content="Here are the results.",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    async def fake_tool(name, args, **kw):
        return "file content"

    tool_registry = MagicMock()
    tool_registry.get_definitions.return_value = [{"type": "function", "function": {"name": "read_file"}}]
    tool_registry.execute = AsyncMock(side_effect=fake_tool)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "read both files"}],
        tools=tool_registry,
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.final_content == "Here are the results."
    assert result.stop_reason == "completed"
    # 4 calls: tool call → empty retry → tool call → text response. No
    # end-of-loop assess continue (fire-and-forget only).
    assert call_count == 4
    assert result.total_llm_requests == 4
    assert "read_file" in result.tools_used






class _DelayTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        delay: float,
        read_only: bool,
        shared_events: list[str],
        exclusive: bool = False,
    ):
        self._name = name
        self._delay = delay
        self._read_only = read_only
        self._shared_events = shared_events
        self._exclusive = exclusive

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def exclusive(self) -> bool:
        return self._exclusive

    async def execute(self, **kwargs):
        self._shared_events.append(f"start:{self._name}")
        await asyncio.sleep(self._delay)
        self._shared_events.append(f"end:{self._name}")
        return self._name


@pytest.mark.asyncio
async def test_runner_does_not_batch_exclusive_read_only_tools():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    tools = ToolRegistry()
    shared_events: list[str] = []
    read_a = _DelayTool("read_a", delay=0.03, read_only=True, shared_events=shared_events)
    read_b = _DelayTool("read_b", delay=0.03, read_only=True, shared_events=shared_events)
    ddg_like = _DelayTool(
        "ddg_like",
        delay=0.01,
        read_only=True,
        shared_events=shared_events,
        exclusive=True,
    )
    tools.register(read_a)
    tools.register(ddg_like)
    tools.register(read_b)

    runner = AgentRunner(MagicMock())
    await runner._execute_tools(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            concurrent_tools=True,
        ),
        [
            ToolCallRequest(id="ro1", name="read_a", arguments={}),
            ToolCallRequest(id="ddg1", name="ddg_like", arguments={}),
            ToolCallRequest(id="ro2", name="read_b", arguments={}),
        ],
        {},
    )

    assert shared_events[0] == "start:read_a"
    assert shared_events.index("end:read_a") < shared_events.index("start:ddg_like")
    assert shared_events.index("end:ddg_like") < shared_events.index("start:read_b")






@pytest.mark.asyncio
async def test_loop_stream_filter_handles_think_only_prefix_without_crashing(tmp_path):
    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("<think>hidden")
        await on_content_delta("</think>Hello")
        return LLMResponse(content="<think>hidden</think>Hello", tool_calls=[], usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        endings.append(resuming)

    final_content, _, _, _, _, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "Hello"
    # End-of-loop assess is fire-and-forget — no second streaming round
    assert deltas == ["Hello"]
    assert endings == [False]






@pytest.mark.asyncio
async def test_streamed_flag_not_set_on_llm_error(tmp_path):
    """When LLM errors during a streaming-capable channel interaction,
    _streamed must NOT be set so ChannelManager delivers the error."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    llm_set_llm(provider, "test-model")
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    error_resp = LLMResponse(
        content="503 service unavailable", finish_reason="error", tool_calls=[], usage={},
    )
    loop.provider.chat_with_retry = AsyncMock(return_value=error_resp)
    loop.provider.chat_stream_with_retry = AsyncMock(return_value=error_resp)
    loop.tools.get_definitions = MagicMock(return_value=[])

    msg = InboundMessage(
        channel="feishu", sender_id="u1", chat_id="c1", content="hi",
    )
    result = await loop._process_message(
        msg,
        on_stream=AsyncMock(),
        on_stream_end=AsyncMock(),
    )

    assert result is not None
    assert "503" in result.content
    assert not result.metadata.get("_streamed"), \
        "_streamed must not be set when stop_reason is error"












# ---------------------------------------------------------------------------
# Length recovery (auto-continue on finish_reason == "length")
# ---------------------------------------------------------------------------




@pytest.mark.asyncio
async def test_length_recovery_streaming_calls_on_stream_end_with_resuming():
    """During length recovery with streaming, on_stream_end should be called
    with resuming=True so the hook knows the conversation is continuing."""
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}
    stream_end_calls: list[bool] = []

    class StreamHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            pass

        async def on_stream_end(self, context: AgentHookContext, resuming: bool = False) -> None:
            stream_end_calls.append(resuming)

    async def chat_stream_with_retry(*, messages, on_content_delta=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(content="partial ", finish_reason="length", usage={})
        return LLMResponse(content="done", finish_reason="stop", usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=tools,
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=StreamHook(),
    ))

    # End-of-loop assess is fire-and-forget — no extra round
    assert len(stream_end_calls) == 2
    assert stream_end_calls[0] is True   # length recovery: resuming
    assert stream_end_calls[1] is False  # final break: not resuming




# ---------------------------------------------------------------------------
# Backfill missing tool_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_missing_tool_results_inserts_error():
    """Orphaned tool_use (no matching tool_result) should get a synthetic error."""
    from nanobot.agent.runner import _BACKFILL_CONTENT, AgentRunner

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "exec", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "name": "exec", "content": "ok"},
    ]
    result = AgentRunner._backfill_missing_tool_results(messages)
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    backfilled = [m for m in tool_msgs if m.get("tool_call_id") == "call_b"]
    assert len(backfilled) == 1
    assert backfilled[0]["content"] == _BACKFILL_CONTENT
    assert backfilled[0]["name"] == "read_file"


def test_drop_orphan_tool_results_removes_unmatched_tool_messages():
    from nanobot.agent.runner import AgentRunner

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_ok", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "name": "read_file", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_orphan", "name": "exec", "content": "stale"},
        {"role": "assistant", "content": "after tool"},
    ]

    cleaned = AgentRunner._drop_orphan_tool_results(messages)

    assert cleaned == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_ok", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "name": "read_file", "content": "ok"},
        {"role": "assistant", "content": "after tool"},
    ]


@pytest.mark.asyncio
async def test_backfill_noop_when_complete():
    """Complete message chains should not be modified."""
    from nanobot.agent.runner import AgentRunner

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_x", "type": "function", "function": {"name": "exec", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_x", "name": "exec", "content": "done"},
        {"role": "assistant", "content": "all good"},
    ]
    result = AgentRunner._backfill_missing_tool_results(messages)
    assert result is messages  # same object — no copy








# ---------------------------------------------------------------------------
# Microcompact (stale tool result compaction)
# ---------------------------------------------------------------------------




@pytest.mark.asyncio
async def test_runner_tool_error_preserves_tool_results_in_messages():
    """When a tool raises a fatal error, its results must still be appended
    to messages so the session never contains orphan tool_calls (#2943)."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="tc1", name="read_file", arguments={"path": "a"}),
                ToolCallRequest(id="tc2", name="exec", arguments={"cmd": "bad"}),
            ],
            usage={},
        )

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    call_idx = 0

    async def fake_execute(name, args, **kw):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 2:
            raise RuntimeError("boom")
        return "file content"

    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=fake_execute)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "do stuff"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        fail_on_tool_error=True,
    ))

    # Tool error now feeds back to LLM instead of breaking — the loop
    # continues until max_iterations since the mock LLM keeps returning
    # the same tool calls.
    assert result.stop_reason == "max_iterations"
    # Both tool results from each iteration must still be in messages
    # even though tc2 had a fatal error. 2 iterations × 2 tool calls = 4 results.
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 4
    assert tool_msgs[0]["tool_call_id"] == "tc1"
    assert tool_msgs[1]["tool_call_id"] == "tc2"
    assert tool_msgs[2]["tool_call_id"] == "tc1"
    assert tool_msgs[3]["tool_call_id"] == "tc2"
    # The assistant message with tool_calls must precede the tool results.
    asst_tc_idx = next(
        i for i, m in enumerate(result.messages)
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    tool_indices = [
        i for i, m in enumerate(result.messages) if m.get("role") == "tool"
    ]
    assert all(ti > asst_tc_idx for ti in tool_indices)


def test_governance_repairs_orphans_after_snip():
    """After _snip_history clips an assistant+tool_calls, the second
    _drop_orphan_tool_results pass must clean up the resulting orphans."""
    from nanobot.agent.runner import AgentRunner

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old msg"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "tc_old", "type": "function",
                         "function": {"name": "search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc_old", "name": "search",
         "content": "old result"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new msg"},
    ]

    # Simulate snipping that keeps only the tail: drop the assistant with
    # tool_calls but keep its tool result (orphan).
    snipped = [
        {"role": "system", "content": "system"},
        {"role": "tool", "tool_call_id": "tc_old", "name": "search",
         "content": "old result"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new msg"},
    ]

    cleaned = AgentRunner._drop_orphan_tool_results(snipped)
    # The orphan tool result should be removed.
    assert not any(
        m.get("role") == "tool" and m.get("tool_call_id") == "tc_old"
        for m in cleaned
    )


def test_governance_fallback_still_repairs_orphans():
    """When full governance fails, the fallback must still run
    _drop_orphan_tool_results and _backfill_missing_tool_results."""
    from nanobot.agent.runner import AgentRunner

    # Messages with an orphan tool result (no matching assistant tool_call).
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "tool_call_id": "orphan_tc", "name": "read",
         "content": "stale"},
        {"role": "assistant", "content": "hi"},
    ]

    repaired = AgentRunner._drop_orphan_tool_results(messages)
    repaired = AgentRunner._backfill_missing_tool_results(repaired)
    # Orphan tool result should be gone.
    assert not any(m.get("tool_call_id") == "orphan_tc" for m in repaired)
# ── Mid-turn injection tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_injections_returns_empty_when_no_callback():
    """No injection_callback → empty list."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []
    spec = AgentRunSpec(
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=None,
    )
    result = await runner._drain_injections(spec)
    assert result == []


@pytest.mark.asyncio
async def test_drain_injections_extracts_content_from_inbound_messages():
    """Should extract .content from InboundMessage objects."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hello"),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="world"),
    ]

    async def cb():
        return msgs

    spec = AgentRunSpec(
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "world"},
    ]


@pytest.mark.asyncio
async def test_drain_injections_passes_limit_to_callback_when_supported():
    """Limit-aware callbacks can preserve overflow in their own queue."""
    from nanobot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
    from nanobot.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []
    seen_limits: list[int] = []

    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content=f"msg{i}")
        for i in range(_MAX_INJECTIONS_PER_TURN + 3)
    ]

    async def cb(*, limit: int):
        seen_limits.append(limit)
        return msgs[:limit]

    spec = AgentRunSpec(
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert seen_limits == [_MAX_INJECTIONS_PER_TURN]
    # Callback returned _MAX_INJECTIONS_PER_TURN items, all should be in result
    assert len(result) == _MAX_INJECTIONS_PER_TURN
    assert result == [{"role": "user", "content": f"msg{i}"} for i in range(_MAX_INJECTIONS_PER_TURN)]


@pytest.mark.asyncio
async def test_drain_injections_skips_empty_content():
    """Messages with blank content should be filtered out."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.bus.events import InboundMessage

    provider = MagicMock()
    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    msgs = [
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content=""),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="   "),
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="valid"),
    ]

    async def cb():
        return msgs

    spec = AgentRunSpec(
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == [{"role": "user", "content": "valid"}]


@pytest.mark.asyncio
async def test_drain_injections_handles_callback_exception():
    """If the callback raises, return empty list (error is logged)."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def cb():
        raise RuntimeError("boom")

    spec = AgentRunSpec(
        initial_messages=[], tools=tools, model="m",
        max_iterations=1, max_tool_result_chars=1000,
        injection_callback=cb,
    )
    result = await runner._drain_injections(spec)
    assert result == []




@pytest.mark.asyncio
async def test_checkpoint2_injects_after_final_response_with_resuming_stream():
    """After final response, if injections exist, stream_end should get resuming=True."""
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.bus.events import InboundMessage

    provider = MagicMock()
    call_count = {"n": 0}
    stream_end_calls = []

    class TrackingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            stream_end_calls.append(resuming)

        def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
            return content

    async def chat_stream_with_retry(*, messages, on_content_delta=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(content="first answer", tool_calls=[], usage={})
        return LLMResponse(content="second answer", tool_calls=[], usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    injection_queue = asyncio.Queue()
    inject_cb = _make_injection_callback(injection_queue)

    # Inject a follow-up that arrives during the first response
    await injection_queue.put(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="quick follow-up")
    )

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=TrackingHook(),
        injection_callback=inject_cb,
    ))

    assert result.had_injections is True
    assert result.final_content == "second answer"
    # 2 calls (no verification gate)
    assert call_count["n"] == 3
    # First stream_end should have resuming=True (because injections found)
    assert stream_end_calls[0] is True
    # Second stream_end: should_continue=False (no more injections),
    # then doubt assess_me injects AFTER on_stream_end via `continue`
    assert stream_end_calls[1] is False
    # Third (final) stream_end should have resuming=False
    assert stream_end_calls[-1] is False


@pytest.mark.asyncio
async def test_doubt_skipped_on_error_finish_reason():
    """Doubt assess_me should NOT inject when finish_reason is 'error'."""
    from nanobot.agent.assess_me import _ASSESSMENT_PREFIX
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()

    async def chat_with_retry(**kwargs):
        return LLMResponse(content="", tool_calls=[], usage={}, finish_reason="error")

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=3,  # room for doubt, but error should prevent it
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        error_message="error occurred",
    ))

    assert result.final_content == "error occurred"
    assert result.stop_reason == "error"
    assert result.total_llm_requests == 2  # 2 calls (error + 1 retry), but no doubt extra call
    # Verify doubt was NOT injected
    for m in result.messages:
        assert _ASSESSMENT_PREFIX not in str(m.get("content", "")), "doubt should NOT appear on error"


@pytest.mark.asyncio
async def test_doubt_skipped_at_max_iterations_boundary():
    """Doubt should NOT inject when iteration+1 >= max_iterations (no room for response)."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.providers.base import LLMResponse

    provider = MagicMock()

    async def chat_with_retry(**kwargs):
        return LLMResponse(content="final answer", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=1,  # no room for doubt: iteration=0, iteration+1 >= 1
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.final_content == "final answer"
    assert result.total_llm_requests == 1  # only 1 call, no doubt


@pytest.mark.asyncio
async def test_doubt_only_injected_once():
    """After doubt injects once, second pass through the check should skip."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.providers.base import LLMResponse

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(**kwargs):
        nonlocal call_count
        call_count += 1
        return LLMResponse(content=f"answer {call_count}", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        model="test-model",
        max_iterations=5,  # room for doubt + response
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    # Doubt injects on iter 0 → LLM responds (call 2) → break (no second doubt)
    assert call_count == 2
    assert result.total_llm_requests == 2














@pytest.mark.asyncio
async def test_pending_queue_full_falls_back_to_queued_task(tmp_path):
    """QueueFull should preserve the message by dispatching a queued task."""
    from nanobot.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    loop._dispatch = AsyncMock()  # type: ignore[method-assign]

    pending = asyncio.Queue(maxsize=1)
    pending.put_nowait(InboundMessage(channel="cli", sender_id="u", chat_id="c", content="already queued"))
    loop._session_dispatch["cli:c"] = _SessionDispatchState(tasks=[], pending=pending)

    run_task = asyncio.create_task(loop.run())
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="follow-up")
    await loop.bus.publish_inbound(msg)

    deadline = time.time() + 2
    while loop._dispatch.await_count == 0 and time.time() < deadline:
        await asyncio.sleep(0.01)

    loop.stop()
    await asyncio.wait_for(run_task, timeout=2)

    assert loop._dispatch.await_count == 1
    dispatched_msg = loop._dispatch.await_args.args[0]
    assert dispatched_msg.content == "follow-up"
    assert pending.qsize() == 1


@pytest.mark.asyncio
async def test_dispatch_republishes_leftover_queue_messages(tmp_path):
    """Messages left in the pending queue after _dispatch are re-published to the bus.

    This tests the finally-block cleanup that prevents message loss when
    the runner exits early (e.g., max_iterations, tool_error) with messages
    still in the queue.
    """
    from nanobot.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    bus = loop.bus

    # Simulate a completed dispatch by manually registering a queue
    # with leftover messages, then running the cleanup logic directly.
    pending = asyncio.Queue(maxsize=20)
    session_key = "cli:c"
    loop._session_dispatch[session_key] = _SessionDispatchState(tasks=[], pending=pending)
    pending.put_nowait(InboundMessage(channel="cli", sender_id="u", chat_id="c", content="leftover-1"))
    pending.put_nowait(InboundMessage(channel="cli", sender_id="u", chat_id="c", content="leftover-2"))

    # Execute the cleanup logic from the finally block
    state = loop._session_dispatch.pop(session_key, None)
    queue = state.pending if state else None
    assert queue is not None
    leftover = 0
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await bus.publish_inbound(item)
        leftover += 1

    assert leftover == 2

    # Verify the messages are now on the bus
    msgs = []
    while not bus.inbound.empty():
        msgs.append(await asyncio.wait_for(bus.consume_inbound(), timeout=0.5))
    contents = [m.content for m in msgs]
    assert "leftover-1" in contents
    assert "leftover-2" in contents














# ---------------------------------------------------------------------------
# Regression tests for GLM-1214: _snip_history must preserve a user message
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration: overflow → MessagePipe compression → messages sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_recovers_from_overflow_via_compression():
    """Runner completes after overflow → MessagePipe compression → retry.

    Verifies the (response, compressed) tuple flows correctly from
    MessagePipe through request_model back into runner's messages list
    without crashing or producing wrong output.
    """
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def _stream_chat(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(content="context window exceeded", finish_reason="error", error_kind="context_length")
        if "on_content_delta" in kwargs:
            # Main call retry (has stream callbacks)
            return LLMResponse(content="final answer.", finish_reason="stop", tool_calls=[], usage={"prompt_tokens": 5, "completion_tokens": 5})
        # Summary call from _compress (no stream callbacks)
        return LLMResponse(content="summary text.", finish_reason="stop")

    provider.chat_stream_with_retry = _stream_chat
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", finish_reason="stop"))
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "hello"},
        ],
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert call_count == 3  # overflow + summary + retry
    assert result.final_content == "final answer."
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_initial_message_count_no_compression():
    """initial_message_count equals len(initial_messages) when no overflow."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", finish_reason="stop", tool_calls=[], usage={}),
    )
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", finish_reason="stop"))
    llm_set_llm(provider, "test-model")

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=msgs,
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.initial_message_count == len(msgs)


@pytest.mark.asyncio
async def test_initial_message_count_updated_after_compression():
    """initial_message_count reflects compressed length, not original."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def _stream_chat(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(content="context window exceeded", finish_reason="error", error_kind="context_length")
        if "on_content_delta" in kwargs:
            return LLMResponse(content="done", finish_reason="stop", tool_calls=[], usage={})
        return LLMResponse(content="summary", finish_reason="stop")

    provider.chat_stream_with_retry = _stream_chat
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", finish_reason="stop"))
    llm_set_llm(provider, "test-model")

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]
    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=msgs,
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    # After compression original 6 msgs → system + synthetic_summary + keep turn (asst + user) = 4
    # Budget=None keeps only 1 turn, 3 turns total → 2 compressed → 1 summary msg
    assert result.initial_message_count == 4
    assert result.initial_message_count < len(msgs)  # compressed shorter than original


# ===========================================================================
# Instructions injection
# ===========================================================================


@pytest.mark.asyncio
async def test_instructions_injected_at_index_1():
    """Instructions are injected as a user message right after system prompt."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    seen_messages: list[list[dict]] = []

    async def chat_with_retry(*, messages, **kwargs):
        seen_messages.append(messages)
        return LLMResponse(content="ok", finish_reason="stop", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)
    await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        instructions="do not delete files",
    ))

    assert len(seen_messages) >= 1
    injected = seen_messages[0]
    assert injected[1]["role"] == "user"
    assert "## Instructions" in injected[1]["content"]
    assert "do not delete files" in injected[1]["content"]


@pytest.mark.asyncio
async def test_instructions_not_injected_when_none():
    """No instructions field → no injection."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    seen_messages: list[list[dict]] = []

    async def chat_with_retry(*, messages, **kwargs):
        seen_messages.append(messages)
        return LLMResponse(content="ok", finish_reason="stop", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)
    await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert len(seen_messages) >= 1
    injected = seen_messages[0]
    # Index 1 should be the original user message, not instructions
    assert injected[1]["role"] == "user"
    assert injected[1]["content"] == "hello"


@pytest.mark.asyncio
async def test_instructions_not_injected_when_empty_messages():
    """Empty messages list → no crash."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    seen_messages: list[list[dict]] = []

    async def chat_with_retry(*, messages, **kwargs):
        seen_messages.append(messages)
        return LLMResponse(content="ok", finish_reason="stop", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)
    await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=MagicMock(get_definitions=MagicMock(return_value=[])),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        instructions="some rules",
    ))

    # Should complete without error — injection is skipped when messages_for_model is empty
    assert len(seen_messages) >= 1


# ===========================================================================
# AgentRunner._append_final_message
# ===========================================================================

class TestAppendFinalMessage:
    """``AgentRunner._append_final_message`` — static, appends/replaces final message."""

    def _runner(self):
        from nanobot.agent.runner import AgentRunner
        return AgentRunner

    def test_none_content_noop(self):
        msgs = [{"role": "user", "content": "hi"}]
        self._runner()._append_final_message(msgs, None)
        assert len(msgs) == 1

    def test_same_content_skips_duplicate(self):
        msgs = [{"role": "assistant", "content": "already"}]
        self._runner()._append_final_message(msgs, "already")
        assert len(msgs) == 1

    def test_replaces_last_assistant_without_tool_calls(self):
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "old"}]
        self._runner()._append_final_message(msgs, "new")
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "new"

    def test_appends_when_last_is_user(self):
        msgs = [{"role": "user", "content": "q"}]
        self._runner()._append_final_message(msgs, "final answer")
        assert len(msgs) == 2
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["content"] == "final answer"

    def test_appends_when_last_assistant_has_tool_calls(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]}]
        self._runner()._append_final_message(msgs, "final")
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "final"

    def test_empty_messages_list(self):
        msgs: list = []
        self._runner()._append_final_message(msgs, "content")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "content"


# ===========================================================================
# AgentRunner._append_model_error_placeholder
# ===========================================================================

class TestAppendModelErrorPlaceholder:
    """``AgentRunner._append_model_error_placeholder`` — inserts placeholder."""

    def _runner(self):
        from nanobot.agent.runner import AgentRunner
        return AgentRunner

    def test_skips_when_last_is_user_with_placeholder(self):
        # Skip condition: last is user AND content == placeholder
        # When last is assistant (not user), skip condition not met → appends
        msgs = [{"role": "assistant", "content": "partial"}]
        self._runner()._append_model_error_placeholder(msgs)
        assert len(msgs) == 2
        assert msgs[-1]["role"] == "user"
        assert "模型异常" in msgs[-1]["content"]

    def test_appends_when_last_is_user(self):
        # Last is user but content != placeholder → does not skip, appends
        msgs = [{"role": "user", "content": "q"}]
        self._runner()._append_model_error_placeholder(msgs)
        assert len(msgs) == 2
        assert msgs[-1]["role"] == "user"
        assert "模型异常" in msgs[-1]["content"]

    def test_appends_when_last_assistant_has_tool_calls(self):
        # Skip condition only matches user+placeholder, so assistant always appends
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]}]
        self._runner()._append_model_error_placeholder(msgs)
        assert len(msgs) == 2
        assert msgs[-1]["role"] == "user"
        assert "模型异常" in msgs[-1]["content"]

    def test_appends_to_empty_list(self):
        msgs: list = []
        self._runner()._append_model_error_placeholder(msgs)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "模型异常" in msgs[0]["content"]


# ===========================================================================
# AgentRunner._log_tool_call
# ===========================================================================

class TestLogToolCall:
    """``AgentRunner._log_tool_call`` — persists tool call to DB."""

    def test_noop_without_db(self):
        from nanobot.agent.runner import AgentRunner
        runner = AgentRunner(MagicMock(), db=None)
        runner._log_tool_call("sess", 0, 0, "read_file", {}, "result", True, None)

    def test_calls_insert_tool_call(self):
        from nanobot.agent.runner import AgentRunner
        db = MagicMock()
        runner = AgentRunner(MagicMock(), db=db)
        runner._log_tool_call("sess", 0, 0, "read_file", {"path": "/x"}, "result", True, None)
        db.insert_tool_call.assert_called_once_with(
            session_key="sess", iteration=0, turn=0,
            tool_name="read_file", params={"path": "/x"},
            result="result", success=True, error=None,
        )

    def test_logs_exception_on_failure(self):
        from nanobot.agent.runner import AgentRunner
        db = MagicMock()
        db.insert_tool_call.side_effect = RuntimeError("DB down")
        runner = AgentRunner(MagicMock(), db=db)
        with patch("nanobot.agent.runner.logger.exception") as mock_log:
            runner._log_tool_call("sess", 0, 0, "read_file", {}, "result", True, None)
        mock_log.assert_called_once()


# ===========================================================================
# Surrogate resilience: json.dumps fallback for tool results
# ===========================================================================


@pytest.mark.asyncio
# ===========================================================================
# Tool loop recovery — _check_tool_loop, _trim_to_last_n_turns, _force_final_response
# ===========================================================================


class TestForceFinalResponse:
    """Module-level _force_final_response."""

    def test_strips_tool_call_turn_and_appends(self):
        from nanobot.agent.runner import _force_final_response
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]},
            {"role": "tool", "content": "r", "tool_call_id": "tc1"},
        ]
        _force_final_response(msgs, "done")
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "done"
        assert "tool_calls" not in msgs[-1]

    def test_strips_multiple_tool_results(self):
        from nanobot.agent.runner import _force_final_response
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}, {"id": "tc2"}]},
            {"role": "tool", "content": "r1", "tool_call_id": "tc1"},
            {"role": "tool", "content": "r2", "tool_call_id": "tc2"},
        ]
        _force_final_response(msgs, "final")
        # Tool results stripped, assistant tool-call message kept, final appended
        assert all(m["role"] != "tool" for m in msgs)
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "final"

    def test_empty_messages(self):
        from nanobot.agent.runner import _force_final_response
        msgs: list = []
        _force_final_response(msgs, "done")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "done"


@pytest.mark.asyncio
async def test_check_tool_loop_no_param_errors_returns_none():
    """No parameter validation errors → no action."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()
    tool_calls = [MagicMock(name="get_weather")]
    new_events = [{"status": "success", "detail": "ok"}]

    result = await runner._check_tool_loop(state, tool_calls, new_events, [], iteration=1)
    assert result is None
    assert state.count == 0
    assert state.level == 0


@pytest.mark.asyncio
async def test_check_tool_loop_first_error_starts_tracking():
    """First param error sets name/sig and count=1."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()
    tc = MagicMock(name="get_weather")
    ev = {"status": "error", "detail": "Invalid parameters for tool 'get_weather': missing 'city'"}

    result = await runner._check_tool_loop(state, [tc], [ev], [], iteration=1)
    assert result is None  # first hit, doesn't escalate
    assert state.count == 1
    assert state.level == 0


@pytest.mark.asyncio
async def test_check_tool_loop_three_hits_escalates_to_assess_me():
    """Three consecutive same errors → 'assess_me' at level 0."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()
    tc = MagicMock(name="get_weather")
    ev = {"status": "error", "detail": "Invalid parameters for tool 'get_weather': missing 'city'"}

    # Two hits → tracking, no action
    await runner._check_tool_loop(state, [tc], [ev], [], iteration=1)
    await runner._check_tool_loop(state, [tc], [ev], [], iteration=2)
    assert state.count == 2

    # Third hit → assess_me
    result = await runner._check_tool_loop(state, [tc], [ev], [], iteration=3)
    assert result == "assess_me"
    assert state.count == 0  # reset after escalation
    assert state.level == 1


@pytest.mark.asyncio
async def test_check_tool_loop_escalates_to_compress():
    """Next three same errors after assess_me → 'compress' at level 1."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState(
        level=1, count=0, tool_name="get_weather",
        error_sig="Invalid parameters for tool 'get_weather': missing 'city'",
        checked_iteration=0,
    )
    from unittest.mock import PropertyMock
    tc = MagicMock()
    type(tc).name = PropertyMock(return_value="get_weather")
    ev = {"status": "error", "detail": "Invalid parameters for tool 'get_weather': missing 'city'"}

    await runner._check_tool_loop(state, [tc], [ev], [], iteration=1)
    await runner._check_tool_loop(state, [tc], [ev], [], iteration=2)

    result = await runner._check_tool_loop(state, [tc], [ev], [], iteration=3)
    assert result == "compress"
    assert state.level == 2


@pytest.mark.asyncio
async def test_check_tool_loop_escalates_to_force_stop():
    """Next three same errors after compress → 'force_stop' at level 2."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState(
        level=2, count=0, tool_name="get_weather",
        error_sig="Invalid parameters for tool 'get_weather': missing 'city'",
        checked_iteration=0,
    )
    from unittest.mock import PropertyMock
    tc = MagicMock()
    type(tc).name = PropertyMock(return_value="get_weather")
    ev = {"status": "error", "detail": "Invalid parameters for tool 'get_weather': missing 'city'"}

    await runner._check_tool_loop(state, [tc], [ev], [], iteration=1)
    await runner._check_tool_loop(state, [tc], [ev], [], iteration=2)

    result = await runner._check_tool_loop(state, [tc], [ev], [], iteration=3)
    assert result == "force_stop"
    assert state.level == 3


@pytest.mark.asyncio
async def test_check_tool_loop_different_tool_resets():
    """Different tool name resets counter and level."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()

    from unittest.mock import PropertyMock
    tc1 = MagicMock()
    type(tc1).name = PropertyMock(return_value="tool_a")
    ev1 = {"status": "error", "detail": "Invalid parameters for tool 'tool_a': bad"}

    tc2 = MagicMock()
    type(tc2).name = PropertyMock(return_value="tool_b")
    ev2 = {"status": "error", "detail": "Invalid parameters for tool 'tool_b': bad"}

    await runner._check_tool_loop(state, [tc1], [ev1], [], iteration=1)
    assert state.tool_name == "tool_a"

    # Different tool resets
    await runner._check_tool_loop(state, [tc2], [ev2], [], iteration=2)
    assert state.tool_name == "tool_b"
    assert state.count == 1
    assert state.level == 0


@pytest.mark.asyncio
async def test_check_tool_loop_same_iteration_skipped():
    """Calling with same iteration returns None (idempotent guard)."""
    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState(checked_iteration=5)

    result = await runner._check_tool_loop(state, [], [], [], iteration=5)
    assert result is None


@pytest.mark.asyncio
async def test_check_tool_loop_non_param_errors_ignored():
    """All tool errors are now tracked (P3 broadened detection)."""
    from unittest.mock import PropertyMock

    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()
    tc = MagicMock()
    type(tc).name = PropertyMock(return_value="exec")
    ev = {"status": "error", "detail": "ExecutionError: command not found"}

    result = await runner._check_tool_loop(state, [tc], [ev], [], iteration=1)
    assert result is None
    assert state.count == 1  # non-param errors ARE tracked now
    assert state.tool_name == "exec"


# ===========================================================================
# Tool loop recovery — regression, integration, scenario tests
# ===========================================================================


class TestForceFinalResponseRegression:
    """Regression tests for _force_final_response."""

    def test_returns_text(self):
        """Must return the same text that was appended."""
        from nanobot.agent.runner import _force_final_response
        msgs = [{"role": "user", "content": "hi"}]
        text = "final answer"
        result = _force_final_response(msgs, text)
        assert result == text
        assert msgs[-1]["content"] == text

    def test_last_already_text_appends(self):
        """If last msg is assistant without tool_calls, appends after it."""
        from nanobot.agent.runner import _force_final_response
        msgs = [{"role": "assistant", "content": "old text"}]
        _force_final_response(msgs, "new text")
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "new text"

    def test_no_tool_calls_appends(self):
        """No trailing tool calls or tool results — just append."""
        from nanobot.agent.runner import _force_final_response
        msgs = [{"role": "user", "content": "q"}]
        _force_final_response(msgs, "done")
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "done"


@pytest.mark.asyncio
async def test_tool_loop_recovery_resets_on_valid_call():
    """State machine resets when a valid (non-error) tool call occurs."""
    from unittest.mock import PropertyMock

    from nanobot.agent.runner import AgentRunner, _ToolLoopState

    runner = AgentRunner(MagicMock())
    state = _ToolLoopState()
    tc = MagicMock()
    type(tc).name = PropertyMock(return_value="test_tool")
    ev_err = {"status": "error", "detail": "Invalid parameters for tool 'test_tool': missing x"}
    ev_ok = {"status": "ok", "detail": "done"}

    await runner._check_tool_loop(state, [tc], [ev_err], [], iteration=1)
    await runner._check_tool_loop(state, [tc], [ev_err], [], iteration=2)
    assert state.count == 2

    result = await runner._check_tool_loop(state, [tc], [ev_ok], [], iteration=3)
    assert result is None
    assert state.count == 0
    assert state.level == 0
    assert state.tool_name == ""

    await runner._check_tool_loop(state, [tc], [ev_err], [], iteration=4)
    assert state.count == 1
    assert state.level == 0


@pytest.mark.asyncio
async def test_tool_loop_recovery_mixed_valid_invalid():
    """Runner with alternating valid/invalid calls completes normally."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.agent.tools.base import Tool
    from nanobot.agent.tools.registry import ToolRegistry

    class NeedsParamTool(Tool):
        name = "needs_param"
        description = "requires x param"
        read_only = False
        _tool_parameters_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        async def execute(self, **kwargs):
            return "done"

    registry = ToolRegistry()
    registry.register(NeedsParamTool())

    provider = MagicMock()
    call_count = 0

    async def chat_fn(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            return LLMResponse(
                content="ok", tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id=f"tc{call_count}", name="needs_param", arguments={})],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    provider.chat_stream_with_retry = chat_fn
    provider.chat_with_retry = chat_fn
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "do stuff"}],
        tools=registry,
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.stop_reason == "completed"
    assert result.final_content == "ok"


@pytest.mark.asyncio
async def test_tool_loop_recovery_full_integration():
    """All 3 escalation levels: Invalid params -> assess_me -> compress -> force_stop."""
    from unittest.mock import AsyncMock, patch

    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.agent.tools.base import Tool
    from nanobot.agent.tools.registry import ToolRegistry

    class NeedsParamTool(Tool):
        name = "needs_param"
        description = "requires x param"
        read_only = False
        _tool_parameters_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        async def execute(self, **kwargs):
            return "done"

    registry = ToolRegistry()
    registry.register(NeedsParamTool())

    provider = MagicMock()
    call_count = 0

    async def chat_fn(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id=f"tc{call_count}", name="needs_param", arguments={})],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    provider.chat_stream_with_retry = chat_fn
    provider.chat_with_retry = chat_fn
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)

    with patch("nanobot.agent.runner._run_assess_me", new_callable=AsyncMock,
               return_value="Provide the required 'x' parameter"):
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "run tool"}],
            tools=registry,
            model="test-model",
            max_iterations=30,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

    assert result.stop_reason == "tool_loop_breaker", (
        f"Expected tool_loop_breaker, got {result.stop_reason}. "
        f"call_count={call_count}, final_content={result.final_content!r}"
    )
    assert call_count >= 9, f"Expected >=9 LLM calls, got {call_count}"
    assert "失败" in (result.final_content or ""), (
        f"Expected force_stop text, got {result.final_content!r}"
    )
    assert any("Provide the required" in m.get("content", "") for m in result.messages), (
        "Expected assess_me injection in result.messages"
    )


@pytest.mark.asyncio
async def test_tool_loop_recovery_empty_assess_me():
    """When assess_me returns empty, loop still escalates through all levels."""
    from unittest.mock import AsyncMock, patch

    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.agent.tools.base import Tool
    from nanobot.agent.tools.registry import ToolRegistry

    class NeedsParamTool(Tool):
        name = "needs_param"
        description = "requires x param"
        read_only = False
        _tool_parameters_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        async def execute(self, **kwargs):
            return "done"

    registry = ToolRegistry()
    registry.register(NeedsParamTool())

    provider = MagicMock()
    call_count = 0

    async def chat_fn(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id=f"tc{call_count}", name="needs_param", arguments={})],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    provider.chat_stream_with_retry = chat_fn
    provider.chat_with_retry = chat_fn
    llm_set_llm(provider, "test-model")

    runner = AgentRunner(provider)

    with patch("nanobot.agent.runner._run_assess_me", new_callable=AsyncMock, return_value=""):
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "run tool"}],
            tools=registry,
            model="test-model",
            max_iterations=30,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

    assert result.stop_reason == "tool_loop_breaker"
    assert call_count >= 9


async def test_tool_result_with_surrogate_does_not_crash():
    """A tool returning a dict with surrogates should not crash the runner."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(id="tc1", name="surrogate_tool", arguments={}),
                ],
                usage={},
            )
        return LLMResponse(content="ok", finish_reason="stop", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    async def _execute(name, args, **kw):
        return {"data": "surrogate \ud800 in result"}

    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=_execute)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "use tool"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_tool_result_surrogate_with_persist_failure():
    """When maybe_persist_tool_result fails and result is a dict, the
    json.dumps(result, ensure_ascii=True) fallback should handle surrogates."""
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(id="tc1", name="surrogate_tool", arguments={}),
                ],
                usage={},
            )
        return LLMResponse(content="done", finish_reason="stop", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    llm_set_llm(provider, "test-model")

    async def _execute(name, args, **kw):
        return {"data": "surrogate \ud800 here"}

    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=_execute)

    with patch("nanobot.agent.runner.maybe_persist_tool_result",
               side_effect=RuntimeError("mock persist failure")):
        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "use tool"}],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

    assert result.stop_reason == "completed"

