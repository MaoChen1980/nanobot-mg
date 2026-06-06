"""Tests for HeartbeatService._tick (message publishing, task tree reading)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.heartbeat.service import HeartbeatService


@pytest.fixture
def service():
    agent_loop = MagicMock()
    agent_loop.workspace = MagicMock()
    agent_loop.context = MagicMock()
    agent_loop.context.timezone = "Asia/Shanghai"
    agent_loop.bus = MagicMock()
    agent_loop.bus.publish_inbound = AsyncMock()
    return HeartbeatService(agent_loop=agent_loop, enabled=True, interval_s=60)


class TestTick:
    @pytest.mark.asyncio
    async def test_skips_when_disabled(self):
        agent_loop = MagicMock()
        srv = HeartbeatService(agent_loop=agent_loop, enabled=False)
        await srv._tick()
        agent_loop.bus.publish_inbound.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_inbound_message_with_task_tree(self, service, tmp_path):
        tree_dir = tmp_path / "tasks"
        tree_dir.mkdir()
        tree_file = tree_dir / "TREE.md"
        tree_file.write_text("- [ ] task 1\n- [x] task 2", encoding="utf-8")
        service.agent_loop.workspace = tmp_path

        with patch("nanobot.utils.helpers.current_time_str", return_value="2025-01-01 12:00"):
            await service._tick()

        service.agent_loop.bus.publish_inbound.assert_awaited_once()
        msg = service.agent_loop.bus.publish_inbound.call_args[0][0]
        assert "task 1" in msg.content
        assert msg.ephemeral is True

    @pytest.mark.asyncio
    async def test_fallback_when_tree_missing(self, service, tmp_path):
        service.agent_loop.workspace = tmp_path

        await service._tick()

        service.agent_loop.bus.publish_inbound.assert_awaited_once()
        msg = service.agent_loop.bus.publish_inbound.call_args[0][0]
        assert "no active tasks" in msg.content

    @pytest.mark.asyncio
    async def test_fallback_when_tree_read_fails(self, service):
        mock_tree = MagicMock()
        mock_tree.exists.return_value = True
        mock_tree.read_text.side_effect = PermissionError("denied")
        mock_tasks_dir = MagicMock()
        mock_tasks_dir.__truediv__.return_value = mock_tree
        mock_workspace = MagicMock()
        mock_workspace.__truediv__.return_value = mock_tasks_dir
        service.agent_loop.workspace = mock_workspace

        await service._tick()

        service.agent_loop.bus.publish_inbound.assert_awaited_once()
        msg = service.agent_loop.bus.publish_inbound.call_args[0][0]
        assert "no active tasks" in msg.content

    @pytest.mark.asyncio
    async def test_uses_session_key_override(self, service):
        await service._tick()
        msg = service.agent_loop.bus.publish_inbound.call_args[0][0]
        assert msg.session_key_override == "cli:direct"
