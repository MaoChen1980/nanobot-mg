"""Tests for /restart slash command."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.loop import _SessionDispatchState
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.providers.base import LLMResponse


def _make_loop():
    """Create a minimal AgentLoop with mocked dependencies."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager"):
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop, bus


async def _wait_until(predicate, *, timeout: float = 0.2, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    assert predicate()


class TestRestartCommand:


    @pytest.mark.asyncio
    async def test_restart_intercepted_in_run_loop(self):
        """Verify /restart is handled at the run-loop level, not inside _dispatch."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/restart")

        with patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Restarting" in out.content

    @pytest.mark.asyncio
    async def test_status_intercepted_in_run_loop(self):
        """Verify /status is handled at the run-loop level for immediate replies."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")

        with patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "nanobot" in out.content.lower() or "Model" in out.content

    @pytest.mark.asyncio
    async def test_run_propagates_external_cancellation(self):
        """External task cancellation should not be swallowed by the inbound wait loop."""
        loop, _bus = _make_loop()

        run_task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.1)
        run_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1.0)



