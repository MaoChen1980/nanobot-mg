"""Tests for AgentLoop public API properties for command handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop


class TestPublicAPI:
    """Verify public properties/methods delegate correctly to private attributes."""

    def test_last_usage_delegates(self):
        loop = MagicMock(spec=AgentLoop)
        loop._last_usage = {"prompt_tokens": 100}
        result = AgentLoop.last_usage.fget(loop)
        assert result == {"prompt_tokens": 100}

    def test_start_time_delegates(self):
        loop = MagicMock(spec=AgentLoop)
        loop._start_time = 12345.0
        result = AgentLoop.start_time.fget(loop)
        assert result == 12345.0

    def test_active_tasks_delegates(self):
        loop = MagicMock(spec=AgentLoop)
        loop._active_tasks = {"key1": []}
        result = AgentLoop.active_tasks.fget(loop)
        assert result == {"key1": []}

    @pytest.mark.asyncio
    async def test_cancel_active_tasks_delegates(self):
        loop = MagicMock(spec=AgentLoop)
        loop._cancel_active_tasks = AsyncMock(return_value=3)
        result = await AgentLoop.cancel_active_tasks(loop, "test_key")
        assert result == 3
        loop._cancel_active_tasks.assert_awaited_once_with("test_key")
