"""Tests for AgentLoop _cancel_active_tasks and stop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop, _SessionDispatchState


class TestCancelActiveTasks:
    @pytest.mark.asyncio
    async def test_no_state_returns_zero(self):
        loop = MagicMock(spec=AgentLoop)
        loop._session_dispatch = {}
        loop.subagents = MagicMock()
        loop.subagents.cancel_by_session = AsyncMock(return_value=0)
        result = await AgentLoop._cancel_active_tasks(loop, "nonexistent")
        assert result == 0

    @pytest.mark.asyncio
    async def test_cancels_and_awaits_tasks(self):
        loop = MagicMock(spec=AgentLoop)
        loop.subagents = MagicMock()
        loop.subagents.cancel_by_session = AsyncMock(return_value=0)

        async def never_ends():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(never_ends())
        pending = MagicMock()
        loop._session_dispatch = {"key1": _SessionDispatchState(tasks=[task], pending=pending)}

        result = await AgentLoop._cancel_active_tasks(loop, "key1")
        assert result >= 1

    @pytest.mark.asyncio
    async def test_cancelled_error_caught(self):
        loop = MagicMock(spec=AgentLoop)
        loop.subagents = MagicMock()
        loop.subagents.cancel_by_session = AsyncMock(return_value=0)

        async def cancels_itself():
            raise asyncio.CancelledError()

        task = asyncio.create_task(cancels_itself())
        await asyncio.sleep(0.01)
        pending = MagicMock()
        loop._session_dispatch = {"key2": _SessionDispatchState(tasks=[task], pending=pending)}

        result = await AgentLoop._cancel_active_tasks(loop, "key2")
        assert result >= 0

    @pytest.mark.asyncio
    async def test_includes_subagent_cancellations(self):
        loop = MagicMock(spec=AgentLoop)
        loop._session_dispatch = {}
        loop.subagents = MagicMock()
        loop.subagents.cancel_by_session = AsyncMock(return_value=3)

        result = await AgentLoop._cancel_active_tasks(loop, "key3")
        assert result == 3
        loop.subagents.cancel_by_session.assert_awaited_once_with("key3")


class TestStop:
    def test_sets_running_to_false(self):
        loop = MagicMock(spec=AgentLoop)
        loop._running = True
        AgentLoop.stop(loop)
        assert not loop._running
