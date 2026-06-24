"""Tests for the Nanobot programmatic facade."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.hook import SDKCaptureHook
from nanobot.nanobot import Nanobot, RunResult, RunStream, StreamEvent


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    data = {
        "providers": {"openrouter": {"apiKey": "sk-test-key"}},
        "agents": {"defaults": {"model": "openai/gpt-4.1"}},
    }
    if overrides:
        data.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))
    return config_path


def test_from_config_missing_file():
    with pytest.raises(FileNotFoundError):
        Nanobot.from_config("/nonexistent/config.json")


def test_from_config_creates_instance(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)
    assert bot._loop is not None
    assert bot._loop.workspace == tmp_path


def test_from_config_default_path():
    from nanobot.config.schema import Config

    with patch("nanobot.config.loader.load_config") as mock_load, \
         patch("nanobot.nanobot._make_provider") as mock_prov:
        mock_load.return_value = Config()
        mock_prov.return_value = MagicMock()
        mock_prov.return_value.get_default_model.return_value = "test"
        mock_prov.return_value.generation.max_tokens = 4096
        Nanobot.from_config()
        mock_load.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_run_returns_result(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    from nanobot.bus.events import OutboundMessage

    mock_response = OutboundMessage(
        channel="cli", chat_id="direct", content="Hello back!"
    )
    bot._loop.process_direct = AsyncMock(return_value=mock_response)

    result = await bot.run("hi")

    assert isinstance(result, RunResult)
    assert result.content == "Hello back!"
    bot._loop.process_direct.assert_awaited_once()
    args, kwargs = bot._loop.process_direct.await_args
    assert args == ("hi",)
    assert kwargs["session_key"] == "sdk:default"
    # SDKCaptureHook is always injected by run()
    assert len(kwargs["extra_hooks"]) == 1
    from nanobot.agent.hook import SDKCaptureHook
    assert isinstance(kwargs["extra_hooks"][0], SDKCaptureHook)


@pytest.mark.asyncio
async def test_run_with_hooks(tmp_path):
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.bus.events import OutboundMessage

    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    class TestHook(AgentHook):
        async def before_iteration(self, context: AgentHookContext) -> None:
            pass

    mock_response = OutboundMessage(
        channel="cli", chat_id="direct", content="done"
    )
    bot._loop.process_direct = AsyncMock(return_value=mock_response)

    result = await bot.run("hi", hooks=[TestHook()])

    assert result.content == "done"
    # Per-call hooks must not leak into the loop's permanent hook list
    assert not any(isinstance(h, TestHook) for h in bot._loop._extra_hooks)
    # hooks=[TestHook()] + SDKCaptureHook = 2 hooks passed
    bot._loop.process_direct.assert_awaited_once()
    _, call_kwargs = bot._loop.process_direct.await_args
    assert len(call_kwargs["extra_hooks"]) == 2
    assert isinstance(call_kwargs["extra_hooks"][0], type(TestHook()))  # user hook first
    assert isinstance(call_kwargs["extra_hooks"][1], SDKCaptureHook)  # capture hook second


@pytest.mark.asyncio
async def test_run_hooks_restored_on_error(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    from nanobot.agent.hook import AgentHook

    bot._loop.process_direct = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await bot.run("hi", hooks=[AgentHook()])


@pytest.mark.asyncio
async def test_run_none_response(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)
    bot._loop.process_direct = AsyncMock(return_value=None)

    result = await bot.run("hi")
    assert result.content == ""


def test_workspace_override(tmp_path):
    config_path = _write_config(tmp_path)
    custom_ws = tmp_path / "custom_workspace"
    custom_ws.mkdir()

    bot = Nanobot.from_config(config_path, workspace=custom_ws)
    assert bot._loop.workspace == custom_ws


def test_sdk_make_provider_uses_github_copilot_backend():
    from nanobot.config.schema import Config
    from nanobot.nanobot import _make_provider

    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "github-copilot",
                    "model": "github-copilot/gpt-4.1",
                }
            }
        }
    )

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = _make_provider(config)

    assert provider.__class__.__name__ == "GitHubCopilotProvider"


@pytest.mark.asyncio
async def test_run_custom_session_key(tmp_path):
    from nanobot.bus.events import OutboundMessage

    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    mock_response = OutboundMessage(
        channel="cli", chat_id="direct", content="ok"
    )
    bot._loop.process_direct = AsyncMock(return_value=mock_response)

    await bot.run("hi", session_key="user-alice")
    bot._loop.process_direct.assert_awaited_once()
    _, call_kwargs = bot._loop.process_direct.await_args
    assert call_kwargs["session_key"] == "user-alice"
    assert len(call_kwargs["extra_hooks"]) == 1
    assert isinstance(call_kwargs["extra_hooks"][0], SDKCaptureHook)


def test_import_from_top_level():
    from nanobot import Nanobot as N, RunResult as R
    assert N is Nanobot
    assert R is RunResult


@pytest.mark.asyncio
async def test_stream_returns_runstream(tmp_path):
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    from nanobot.bus.events import OutboundMessage
    mock_response = OutboundMessage(channel="cli", chat_id="direct", content="ok")
    bot._loop.process_direct = AsyncMock(return_value=mock_response)

    stream = bot.stream("hi")
    assert isinstance(stream, RunStream)
    bot._loop.process_direct.assert_called_once()
    call_args, call_kwargs = bot._loop.process_direct.call_args
    assert len(call_kwargs["extra_hooks"]) == 1
    assert isinstance(call_kwargs["extra_hooks"][0], SDKCaptureHook)


@pytest.mark.asyncio
async def test_stream_with_hooks(tmp_path):
    from nanobot.agent.hook import AgentHook
    config_path = _write_config(tmp_path)
    bot = Nanobot.from_config(config_path, workspace=tmp_path)

    class TestHook(AgentHook):
        pass

    from nanobot.bus.events import OutboundMessage
    bot._loop.process_direct = AsyncMock(
        return_value=OutboundMessage(channel="cli", chat_id="direct", content="ok")
    )

    stream = bot.stream("hi", hooks=[TestHook()])
    assert isinstance(stream, RunStream)
    bot._loop.process_direct.assert_called_once()
    _, call_kwargs = bot._loop.process_direct.call_args
    assert len(call_kwargs["extra_hooks"]) == 2  # user hook + capture hook
    assert isinstance(call_kwargs["extra_hooks"][0], TestHook)
    assert isinstance(call_kwargs["extra_hooks"][1], SDKCaptureHook)


class TestRunStream:
    """Contract tests for RunStream — uses mock internals."""

    @pytest.mark.asyncio
    async def test_stream_events_yields_text_delta(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def mock_task():
            queue.put_nowait(("text.delta", "hello"))
            queue.put_nowait(None)

        task = asyncio.create_task(mock_task())
        stream = RunStream(task=task, queue=queue, capture=capture)

        events = [ev async for ev in stream.stream_events()]
        assert len(events) == 1
        assert events[0].type == "text.delta"
        assert events[0].data == "hello"

    @pytest.mark.asyncio
    async def test_stream_events_raises_on_reentry(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def mock_task():
            queue.put_nowait(None)

        task = asyncio.create_task(mock_task())
        stream = RunStream(task=task, queue=queue, capture=capture)
        async for _ in stream.stream_events():
            pass

        with pytest.raises(RuntimeError, match="already been consumed"):
            async for _ in stream.stream_events():
                pass

    @pytest.mark.asyncio
    async def test_wait_returns_run_result(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def mock_task():
            queue.put_nowait(None)

        task = asyncio.create_task(mock_task())
        stream = RunStream(task=task, queue=queue, capture=capture)

        result = await stream.wait()
        assert isinstance(result, RunResult)
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_wait_idempotent_when_already_consumed(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def mock_task():
            queue.put_nowait(None)

        task = asyncio.create_task(mock_task())
        stream = RunStream(task=task, queue=queue, capture=capture)

        async for _ in stream.stream_events():
            pass
        # Second call should not hang
        result = await stream.wait()
        assert isinstance(result, RunResult)

    @pytest.mark.asyncio
    async def test_cancel_unblocks_consumer(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def never_finish():
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(never_finish())
        stream = RunStream(task=task, queue=queue, capture=capture)

        stream.cancel()
        events = [ev async for ev in stream.stream_events()]
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_text_returns_content(self):
        queue: asyncio.Queue = asyncio.Queue()
        capture = SDKCaptureHook()

        async def mock_task():
            queue.put_nowait(None)

        task = asyncio.create_task(mock_task())
        stream = RunStream(task=task, queue=queue, capture=capture)

        text = await stream.text()
        assert isinstance(text, str)


class TestStreamEvent:
    def test_create_text_delta(self):
        ev = StreamEvent(type="text.delta", data="hello")
        assert ev.type == "text.delta"
        assert ev.data == "hello"

    def test_create_tool_event(self):
        ev = StreamEvent(type="tool.started", data="read_file")
        assert ev.type == "tool.started"

    def test_create_run_completed(self):
        ev = StreamEvent(type="run.completed", data="done")
        assert ev.type == "run.completed"

    def test_create_run_failed(self):
        ev = StreamEvent(type="run.failed", data="error msg")
        assert ev.type == "run.failed"


class TestSDKCaptureHook:
    def test_captures_tool_used(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        rf, wf = MagicMock(), MagicMock()
        rf.name = "read_file"
        wf.name = "write_file"
        context = AgentHookContext(
            iteration=0, messages=[],
            tool_calls=[rf, wf],
        )

        asyncio.run(hook.after_iteration(context))

        assert "read_file" in hook.tools_used
        assert "write_file" in hook.tools_used

    def test_deduplicates_tool_names(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        rf1, rf2 = MagicMock(), MagicMock()
        rf1.name = "read_file"
        rf2.name = "read_file"
        context = AgentHookContext(
            iteration=0, messages=[],
            tool_calls=[rf1, rf2],
        )

        asyncio.run(hook.after_iteration(context))

        assert hook.tools_used == ["read_file"]

    def test_captures_usage(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        context = AgentHookContext(iteration=0, messages=[])
        context.usage = {"input_tokens": 100, "output_tokens": 50}

        asyncio.run(hook.after_iteration(context))

        assert hook.usage == {"input_tokens": 100, "output_tokens": 50}

    def test_captures_stop_reason(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        context = AgentHookContext(iteration=0, messages=[], stop_reason="end_turn")

        asyncio.run(hook.after_iteration(context))

        assert hook.stop_reason == "end_turn"

    def test_captures_error(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        context = AgentHookContext(iteration=0, messages=[], error="rate limited")

        asyncio.run(hook.after_iteration(context))

        assert hook.error == "rate limited"

    def test_captures_messages(self):
        from nanobot.agent.hook import AgentHookContext
        hook = SDKCaptureHook()
        msgs = [{"role": "user", "content": "hi"}]
        context = AgentHookContext(iteration=0, messages=msgs)

        asyncio.run(hook.after_iteration(context))

        assert hook.messages is msgs

    def test_default_state(self):
        hook = SDKCaptureHook()
        assert hook.tools_used == []
        assert hook.messages == []
        assert hook.usage == {}
        assert hook.stop_reason is None
        assert hook.error is None
