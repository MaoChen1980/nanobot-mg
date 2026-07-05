"""Integration-style tests for HeartbeatService._tick — state interaction, error paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.heartbeat.service import HeartbeatService
from nanobot.heartbeat.state import HeartbeatState
from nanobot.agent.context import _sanitize_session_key


class TestTickWithState:
    """Tests that exercise _tick with real HeartbeatState and tree.json file."""

    async def _make_service(self, tmp_path, due_task=None) -> HeartbeatService:
        loop = MagicMock()
        loop.workspace = tmp_path
        loop._session_dispatch = {}
        loop.dispatch_manager = MagicMock()
        loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))

        svc = HeartbeatService(agent_loop=loop, enabled=True, session_key=None)
        svc._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        return svc

    def _write_tree(self, tmp_path, tasks: list[tuple[str, str]]):
        """Write tree.json with pending interval tasks."""
        import json
        tree_dir = tmp_path / "tasks"
        tree_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for name, interval in tasks:
            items.append({"id": name, "name": name, "status": "pending", "interval": interval, "parent": None})
        (tree_dir / "tree.json").write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")

    # --- Cooldown ---

    @pytest.mark.asyncio
    async def test_cooldown_prevents_rapid_ticks(self, tmp_path):
        self._write_tree(tmp_path, [("check", "30m")])
        svc = await self._make_service(tmp_path)
        svc.min_interval_s = 60

        await svc._tick()
        assert svc.agent_loop.process_direct.await_count == 1

        # immediate second tick should be blocked by cooldown
        await svc._tick()
        assert svc.agent_loop.process_direct.await_count == 1

    # --- Multiple tasks, some due ---

    @pytest.mark.asyncio
    async def test_only_due_tasks_in_prompt(self, tmp_path):
        self._write_tree(tmp_path, [("frequent", "10s"), ("hourly", "1h")])
        svc = await self._make_service(tmp_path)

        # hourly has last_run=None (never run), so cooldown check is skipped
        # and it appears unconditionally.
        # frequent also has last_run=None, so it also appears.
        await svc._tick()

        prompt = svc.agent_loop.process_direct.call_args[1]["content"]
        assert "frequent" in prompt
        assert "hourly" in prompt

    # --- TREE.md with leading whitespace ---

    @pytest.mark.asyncio
    async def test_tree_with_whitespace_variations(self, tmp_path):
        import json
        tree_dir = tmp_path / "tasks"
        tree_dir.mkdir(parents=True)
        # Test task name with spaces
        (tree_dir / "tree.json").write_text(
            json.dumps({"items": [{"id": "check_health", "name": "check health", "status": "pending", "interval": "30m", "parent": None}]}),
            encoding="utf-8",
        )
        svc = await self._make_service(tmp_path)
        await svc._tick()
        prompt = svc.agent_loop.process_direct.call_args[1]["content"]
        assert "check health" in prompt


class TestTickErrors:
    """Error-path tests for _tick with mocked workspace."""

    @pytest.mark.asyncio
    async def test_oserror_reading_tree_is_skipped_gracefully(self):
        loop = MagicMock()
        loop._session_dispatch = {}

        mock_file = MagicMock()
        mock_file.exists.return_value = True
        mock_file.read_text.side_effect = OSError("permission denied")

        mock_tasks = MagicMock()
        mock_tasks.__truediv__.return_value = mock_file
        mock_ws = MagicMock()
        mock_ws.__truediv__.return_value = mock_tasks

        loop.workspace = mock_ws
        loop.process_direct = AsyncMock()

        svc = HeartbeatService(agent_loop=loop, enabled=True)
        svc._state = HeartbeatState(mock_tasks / ".heartbeat_state.json")

        await svc._tick()
        loop.process_direct.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_process_direct_does_not_crash(self, tmp_path):
        loop = MagicMock()
        loop.workspace = tmp_path
        loop._session_dispatch = {}
        loop.process_direct = AsyncMock(side_effect=RuntimeError("LLM failed"))
        loop.dispatch_manager = MagicMock()

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "TREE.md").write_text(
            "## active\n- test [interval: 30m]\n## done\n", encoding="utf-8"
        )
        svc = HeartbeatService(agent_loop=loop, enabled=True)
        svc._state = HeartbeatState(tasks_dir / ".heartbeat_state.json")
        svc._last_run = 0.0

        # Should not raise — error is caught inside _tick and logged
        await svc._tick()

    @pytest.mark.asyncio
    async def test_no_interval_tasks_skips_gracefully(self, tmp_path):
        (tmp_path / "tasks").mkdir()
        (tmp_path / "tasks" / "TREE.md").write_text(
            "## active\n- just a normal task\n## done\n", encoding="utf-8"
        )
        loop = MagicMock()
        loop.workspace = tmp_path
        loop._session_dispatch = {}
        loop.process_direct = AsyncMock()
        svc = HeartbeatService(agent_loop=loop, enabled=True)
        svc._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        await svc._tick()
        loop.process_direct.assert_not_called()


class TestRunLoop:
    """Tests for _run_loop — interval, cancellation, error resilience."""

    @pytest.mark.asyncio
    async def test_loop_terminates_when_stopped(self):
        loop = MagicMock()
        svc = HeartbeatService(agent_loop=loop, enabled=True)
        svc._running = True

        call_count = 0

        async def stop_after_sleep(_):
            nonlocal call_count
            call_count += 1
            svc._running = False

        with patch("asyncio.sleep", stop_after_sleep):
            await svc._run_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_tick_does_not_stop_loop(self):
        loop = MagicMock()
        svc = HeartbeatService(agent_loop=loop, enabled=True)
        svc._running = True

        tick_count = 0

        async def controlled_sleep(_):
            nonlocal tick_count
            tick_count += 1
            if tick_count > 1:
                svc._running = False

        with patch.object(svc, "_tick", AsyncMock(side_effect=[ValueError("tick failed"), None])):
            with patch("asyncio.sleep", controlled_sleep):
                await svc._run_loop()

        # ValueError from first tick is swallowed; second tick runs normally
        assert tick_count >= 2
