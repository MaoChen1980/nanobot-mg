import asyncio
from dataclasses import dataclass
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


class MockDB:
    """Minimal DB mock for HeartbeatService testing."""

    def __init__(self, goals: list[dict] | None = None) -> None:
        self._goals = goals or []

    def list_goals(self, status: str | None = None) -> list[dict]:
        if status:
            return [g for g in self._goals if g.get("status") == status]
        return self._goals


class DummyAgentLoop:
    """Minimal AgentLoop mock for HeartbeatService testing."""

    def __init__(self, db: MockDB | None = None) -> None:
        self.bus = FakeBus()
        self._db = db or MockDB()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    loop = DummyAgentLoop(db=MockDB())
    service = HeartbeatService(agent_loop=loop, interval_s=9999, enabled=True)
    await service.start()
    first_task = service._task
    await service.start()
    assert service._task is first_task
    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_tick_publishes_active_goals_to_main_session() -> None:
    goals = [
        {"id": "g1", "title": "Test goal", "status": "in_progress", "subtasks": []},
        {"id": "g2", "title": "Another task", "status": "in_progress", "subtasks": [
            {"id": "s1", "title": "sub1", "status": "done"},
            {"id": "s2", "title": "sub2", "status": "todo"},
        ]},
    ]
    loop = DummyAgentLoop(db=MockDB(goals))
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)

    await service._tick()

    assert len(loop.bus.published) == 1
    msg = loop.bus.published[0]
    assert msg.sender_id == "heartbeat"
    assert msg.chat_id == "direct"
    assert "Test goal" in msg.content
    assert "g1" in msg.content
    assert "Another task" in msg.content
    assert "g2" in msg.content
    assert "write_goal" in msg.content
    assert msg.session_key_override == "cli:direct"


@pytest.mark.asyncio
async def test_tick_empty_when_no_active_goals() -> None:
    loop = DummyAgentLoop(db=MockDB([]))
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)

    await service._tick()

    assert len(loop.bus.published) == 1
    msg = loop.bus.published[0]
    assert "none" in msg.content.lower()


@pytest.mark.asyncio
async def test_tick_does_nothing_when_disabled() -> None:
    loop = DummyAgentLoop(db=MockDB([{"id": "g1", "title": "Test", "status": "in_progress", "subtasks": []}]))
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=False)

    await service._tick()

    assert loop.bus.published == []


@pytest.mark.asyncio
async def test_start_enables_heartbeat() -> None:
    loop = DummyAgentLoop(db=MockDB())
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)
    assert not service._running

    await service.start()
    assert service._running

    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stop_disables_heartbeat() -> None:
    loop = DummyAgentLoop(db=MockDB())
    service = HeartbeatService(agent_loop=loop, interval_s=60, enabled=True)
    await service.start()
    assert service._running

    service.stop()
    assert not service._running
