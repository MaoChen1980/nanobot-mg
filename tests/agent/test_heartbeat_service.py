"""Tests for heartbeat service — skip chain, pending task check, HEARTBEAT_OK."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.heartbeat.service import (
    _HEARTBEAT_OK,
    _is_heartbeat_ok,
    HeartbeatService,
)


def _make_tree_json(tasks_dir: Path, items: list[dict]) -> Path:
    """Write a tree.json with the given items."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / "tree.json"
    path.write_text(json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")
    return path


# =========================================================================
# _is_heartbeat_ok
# =========================================================================


class TestIsHeartbeatOk:
    def test_none_content(self):
        assert _is_heartbeat_ok(None) is True

    def test_empty_content(self):
        assert _is_heartbeat_ok("") is True

    def test_exact_match(self):
        assert _is_heartbeat_ok("HEARTBEAT_OK") is True

    def test_case_insensitive(self):
        assert _is_heartbeat_ok("heartbeat_ok") is True

    def test_with_extra_text(self):
        assert _is_heartbeat_ok("HEARTBEAT_OK nothing to do") is True

    def test_normal_response(self):
        assert _is_heartbeat_ok("I found a bug and fixed it") is False

    def test_partial_word_prefix(self):
        assert _is_heartbeat_ok("HEARTBEAT_OK_THING") is True
        assert _is_heartbeat_ok("XHEARTBEAT_OK") is False

    def test_leading_whitespace(self):
        assert _is_heartbeat_ok("  HEARTBEAT_OK") is True

    def test_whitespace_only(self):
        assert _is_heartbeat_ok("   ") is False


# =========================================================================
# _find_pending_tasks
# =========================================================================


class TestFindPendingTasks:
    def test_returns_empty_when_no_tree(self, tmp_path):
        service = _make_service(agent_loop=_make_loop(tmp_path))
        assert service._find_pending_tasks() == []

    def test_returns_pending_leaves_only(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "root", "status": "active", "parent": None},
            {"id": "child1", "status": "pending", "parent": "root"},
            {"id": "child2", "status": "completed", "parent": "root"},
        ])
        result = service._find_pending_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "child1"

    def test_ignores_pending_with_children(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "parent", "status": "pending", "parent": None},
            {"id": "child", "status": "pending", "parent": "parent"},
        ])
        result = service._find_pending_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "child"

    def test_caps_at_five(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        items = [
            {"id": f"task{i}", "status": "pending", "parent": None}
            for i in range(10)
        ]
        _make_tree_json(tmp_path / "tasks", items)
        result = service._find_pending_tasks()
        assert len(result) == 5

    def test_returns_empty_on_invalid_json(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tasks" / "tree.json").write_text("not json", encoding="utf-8")
        assert service._find_pending_tasks() == []


# =========================================================================
# _build_prompt
# =========================================================================


class TestBuildPrompt:
    def test_contains_pending_tasks(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        pending = [{"id": "t1", "name": "check health", "status": "pending"},
                   {"id": "t2", "name": "sync data", "status": "pending"}]
        prompt = service._build_prompt(pending)
        assert "check health" in prompt
        assert "sync data" in prompt
        assert _HEARTBEAT_OK in prompt

    def test_shows_criteria_and_note(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        pending = [{"id": "t1", "name": "deploy", "status": "pending",
                    "criteria": "all tests pass", "note": "waiting for CI"}]
        prompt = service._build_prompt(pending)
        assert "all tests pass" in prompt
        assert "waiting for CI" in prompt


# =========================================================================
# Skip chain
# =========================================================================


class TestSkipChain:
    async def test_skips_when_disabled(self):
        service = _make_service(enabled=False)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0

    async def test_skips_when_no_tree_json(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0

    async def test_skips_when_no_pending_tasks(self, tmp_path):
        loop = _make_loop(tmp_path)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "root", "status": "completed", "parent": None},
        ])
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0

    async def test_skips_when_session_busy(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop._session_dispatch = {None: MagicMock()}
        _make_tree_json(tmp_path / "tasks", [
            {"id": "task1", "status": "pending", "parent": None},
        ])
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0

    async def test_fires_when_pending_tasks(self, tmp_path):
        loop = _make_loop(tmp_path)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "task1", "status": "pending", "parent": None},
        ])
        loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))
        service = _make_service(agent_loop=loop)
        await service._tick()
        loop.process_direct.assert_awaited_once()

    async def test_updates_last_run_on_fire(self, tmp_path):
        loop = _make_loop(tmp_path)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "task1", "status": "pending", "parent": None},
        ])
        loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run > 0.0


# =========================================================================
# HEARTBEAT_OK response handling
# =========================================================================


class TestHeartbeatOkResponse:
    async def test_sets_content_to_none_on_heartbeat_ok(self, tmp_path):
        loop = _make_loop(tmp_path)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "task1", "status": "pending", "parent": None},
        ])
        response = MagicMock(content="HEARTBEAT_OK all good")
        loop.process_direct = AsyncMock(return_value=response)
        service = _make_service(agent_loop=loop)
        await service._tick()
        assert response.content is None

    async def test_keeps_content_on_normal_response(self, tmp_path):
        loop = _make_loop(tmp_path)
        _make_tree_json(tmp_path / "tasks", [
            {"id": "task1", "status": "pending", "parent": None},
        ])
        response = MagicMock(content="Found an issue, fixed it")
        loop.process_direct = AsyncMock(return_value=response)
        service = _make_service(agent_loop=loop)
        await service._tick()
        assert response.content == "Found an issue, fixed it"


# =========================================================================
# Lifecycle
# =========================================================================


class TestLifecycle:
    async def test_start_does_not_start_when_disabled(self):
        loop = MagicMock()
        service = HeartbeatService(agent_loop=loop, enabled=False)
        await service.start()
        assert service._task is None

    async def test_start_creates_task_when_enabled(self, tmp_path):
        loop = MagicMock()
        loop.workspace = tmp_path
        service = HeartbeatService(agent_loop=loop, enabled=True)
        await service.start()
        assert service._task is not None
        assert service._running is True
        assert service._state is not None
        service.stop()

    def test_stop_cleans_up(self):
        loop = MagicMock()
        service = HeartbeatService(agent_loop=loop, enabled=True)
        service._running = True
        task = MagicMock()
        service._task = task
        service.stop()
        assert service._running is False
        task.cancel.assert_called_once()

    async def test_start_is_idempotent(self, tmp_path):
        loop = MagicMock()
        loop.workspace = tmp_path
        service = HeartbeatService(agent_loop=loop, enabled=True)
        await service.start()
        t1 = service._task
        await service.start()
        assert service._task is t1
        service.stop()

    def test_stop_idempotent_when_not_running(self):
        loop = MagicMock()
        service = HeartbeatService(agent_loop=loop, enabled=True)
        service.stop()  # should not raise
        assert service._running is False


# =========================================================================
# Helpers
# =========================================================================


def _make_service(agent_loop=None, enabled: bool = True, session_key: str | None = None) -> HeartbeatService:
    return HeartbeatService(
        agent_loop=agent_loop or MagicMock(),
        enabled=enabled,
        session_key=session_key,
    )


def _make_loop(tmp_path: Path) -> MagicMock:
    loop = MagicMock()
    loop.workspace = tmp_path
    loop._session_dispatch = {}
    loop.dispatch_manager = MagicMock()
    return loop
