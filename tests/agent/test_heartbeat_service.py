import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from nanobot.heartbeat.service import HeartbeatService
from nanobot.bus.events import InboundMessage


class FakeBus:
    """Minimal bus mock for HeartbeatService testing."""

    def __init__(self) -> None:
        self.published: list[InboundMessage] = []

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self.published.append(msg)


class DummyAgentLoop:
    """Minimal AgentLoop mock for HeartbeatService testing."""

    def __init__(self, workspace=None):
        self.workspace = workspace
        self.bus = FakeBus()

    @property
    def heartbeat_file(self) -> Path:
        return Path(self.workspace) / "HEARTBEAT.md"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    (tmp_path / "HEARTBEAT.md").write_text("- [ ] task", encoding="utf-8")

    service = HeartbeatService(agent_loop=loop, interval_s=9999, enabled=True)
    await service.start()
    first_task = service._task
    await service.start()
    assert service._task is first_task
    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_tick_publishes_to_main_session_via_bus(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    (tmp_path / "HEARTBEAT.md").write_text("- [ ] check deployments", encoding="utf-8")

    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)
    await service._tick()

    assert len(loop.bus.published) == 1
    msg = loop.bus.published[0]
    assert msg.sender_id == "heartbeat"
    assert msg.chat_id == "direct"
    assert "HEARTBEAT.md" in msg.content
    assert "interrupted task" in msg.content
    assert msg.session_key_override == "cli:direct"


@pytest.mark.asyncio
async def test_tick_skips_when_heartbeat_file_missing(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)

    await service._tick()

    assert loop.bus.published == []


@pytest.mark.asyncio
async def test_tick_does_nothing_when_disabled(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    (tmp_path / "HEARTBEAT.md").write_text("- [ ] check deployments", encoding="utf-8")

    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=False)
    await service._tick()

    assert loop.bus.published == []


@pytest.mark.asyncio
async def test_heartbeat_file_property(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)

    assert service.heartbeat_file == tmp_path / "HEARTBEAT.md"


@pytest.mark.asyncio
async def test_start_enables_heartbeat(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)
    assert not service._running

    await service.start()
    assert service._running

    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stop_disables_heartbeat(tmp_path) -> None:
    loop = DummyAgentLoop(workspace=tmp_path)
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)
    await service.start()
    assert service._running

    service.stop()
    assert not service._running
