"""Tests for send_message, send_to_subagent, and related fixes."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.config.schema import AgentDefaults

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars




@pytest.mark.asyncio
async def test_send_message_orchestrator_to_subagent(tmp_path):
    """send_message(recipient='subagent:label') delivers to subagent inbox."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.send_message import SendMessageTool
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    # Register a running subagent with an inbox
    mgr._subagent_label_to_id["test-subagent"] = "test-id"
    mgr._subagent_inboxes["test-id"] = asyncio.Queue()
    task = asyncio.create_task(asyncio.Event().wait())  # "running" task
    mgr._running_tasks["test-id"] = task

    tool = SendMessageTool(manager=mgr)  # no subagent_id = orchestrator mode
    result = await tool.execute(recipient="subagent:test-subagent", message="hello there")

    assert "sent" in result.lower()

    # Verify message landed in the subagent's inbox
    inbox_msg = await asyncio.wait_for(mgr._subagent_inboxes["test-id"].get(), timeout=1.0)
    assert "hello there" in inbox_msg

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_send_to_subagent_toctou_guard(tmp_path):
    """send_to_subagent returns error when subagent already completed."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    # Register a FINISHED subagent (task is done)
    done = asyncio.create_task(asyncio.sleep(0))
    await done  # let it complete
    mgr._subagent_label_to_id["gone-subagent"] = "gone-id"
    mgr._running_tasks["gone-id"] = done
    mgr._subagent_inboxes["gone-id"] = asyncio.Queue()

    result = mgr.send_to_subagent("gone-subagent", "hello")
    assert "already completed" in result or "Error" in result


@pytest.mark.asyncio
async def test_send_to_subagent_unknown_label(tmp_path):
    """send_to_subagent returns error for unknown subagent label."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    result = mgr.send_to_subagent("nobody", "hello")
    assert "no subagent" in result or "Error" in result


@pytest.mark.asyncio
async def test_spawn_many_duplicate_label_rejected(tmp_path):
    """spawn_many rejects duplicate labels."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.spawn_many import SpawnManyTool
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    tool = SpawnManyTool(manager=mgr)
    result = await tool.execute(
        tasks=[
            {"task": "do thing A", "label": "same-label"},
            {"task": "do thing B", "label": "same-label"},
        ]
    )

    assert "duplicate label" in result or "Error" in result



@pytest.mark.asyncio
async def test_subagent_injection_callback_wired(tmp_path):
    """_run_subagent passes injection_callback to AgentRunSpec."""
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    mgr._announce_result = AsyncMock()
    captured_spec = {}

    async def fake_run(spec):
        captured_spec["spec"] = spec
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent(
        "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
        status,
    )

    spec = captured_spec.get("spec")
    assert spec is not None
    assert spec.injection_callback is not None

    # The callback should return empty when inbox is empty
    result = await spec.injection_callback(limit=5)
    assert result == []


@pytest.mark.asyncio
async def test_subagent_injection_callback_receives_messages(tmp_path):
    """injection_callback drains messages from subagent inbox."""
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    mgr._announce_result = AsyncMock()
    captured_spec = {}

    async def fake_run(spec):
        captured_spec["spec"] = spec
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-2", label="label", task_description="do task", started_at=time.monotonic()
    )

    # Put messages in inbox before running
    inbox = mgr._subagent_inboxes["sub-2"] = asyncio.Queue()
    inbox.put_nowait("msg 1")
    inbox.put_nowait("msg 2")

    await mgr._run_subagent(
        "sub-2", "do task", "label", {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
        status,
    )

    spec = captured_spec.get("spec")
    assert spec is not None
    assert spec.injection_callback is not None

    # The callback should return the pre-loaded messages with user role
    result = await spec.injection_callback(limit=10)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert "msg 1" in result[0]["content"]
    assert result[1]["role"] == "user"
    assert "msg 2" in result[1]["content"]
