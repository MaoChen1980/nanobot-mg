"""Tests for the shared agent runner and its integration contracts."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.config.schema import AgentDefaults
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.llm_context import set_llm as llm_set_llm
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.agent.loop import _SessionDispatchState

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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="read_file_tool", arguments={"path": "a.txt"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 2:
            return LLMResponse(content=None, tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 1})
        if call_count == 3:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc2", name="read_file_tool", arguments={"path": "b.txt"})],
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
    tool_registry.get_definitions.return_value = [{"type": "function", "function": {"name": "read_file_tool"}}]
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
    # 4 calls (no verification gate)
    assert call_count == 4
    assert "read_file_tool" in result.tools_used






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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

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

    final_content, _, _, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "Hello"
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

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

    assert len(stream_end_calls) == 2
    assert stream_end_calls[0] is True   # length recovery: resuming
    assert stream_end_calls[1] is False  # final response: done




# ---------------------------------------------------------------------------
# Backfill missing tool_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_missing_tool_results_inserts_error():
    """Orphaned tool_use (no matching tool_result) should get a synthetic error."""
    from nanobot.agent.runner import AgentRunner, _BACKFILL_CONTENT

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "exec_tool", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "read_file_tool", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "name": "exec_tool", "content": "ok"},
    ]
    result = AgentRunner._backfill_missing_tool_results(messages)
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    backfilled = [m for m in tool_msgs if m.get("tool_call_id") == "call_b"]
    assert len(backfilled) == 1
    assert backfilled[0]["content"] == _BACKFILL_CONTENT
    assert backfilled[0]["name"] == "read_file_tool"


def test_drop_orphan_tool_results_removes_unmatched_tool_messages():
    from nanobot.agent.runner import AgentRunner

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_ok", "type": "function", "function": {"name": "read_file_tool", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "name": "read_file_tool", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_orphan", "name": "exec_tool", "content": "stale"},
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
                {"id": "call_ok", "type": "function", "function": {"name": "read_file_tool", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "name": "read_file_tool", "content": "ok"},
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
                {"id": "call_x", "type": "function", "function": {"name": "exec_tool", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_x", "name": "exec_tool", "content": "done"},
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

    provider = MagicMock()

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="tc1", name="read_file_tool", arguments={"path": "a"}),
                ToolCallRequest(id="tc2", name="exec_tool", arguments={"cmd": "bad"}),
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner, _MAX_INJECTIONS_PER_TURN
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner
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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

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
    from nanobot.agent.runner import AgentRunSpec, AgentRunner
    from nanobot.agent.hook import AgentHook, AgentHookContext
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
    assert call_count["n"] == 2
    # First stream_end should have resuming=True (because injections found)
    assert stream_end_calls[0] is True
    # Second (final) stream_end should have resuming=False
    assert stream_end_calls[-1] is False

















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






