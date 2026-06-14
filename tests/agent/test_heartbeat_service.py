"""Tests for heartbeat service — skip chain, TREE.md parsing, HEARTBEAT_OK."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from nanobot.heartbeat.service import (
    _format_duration,
    _HEARTBEAT_OK,
    _is_heartbeat_ok,
    _parse_interval_tasks,
    HeartbeatService,
)
from nanobot.heartbeat.state import HeartbeatState


# =========================================================================
# _parse_interval_tasks
# =========================================================================


class TestParseIntervalTasks:
    def test_parses_interval_tasks_in_active_section(self):
        tree = """## active
- check health [interval: 30m]
- sync data [interval: 1h]
## backlog
- old task [interval: 5m]
"""
        result = _parse_interval_tasks(tree)
        assert result == [("check health", 1800), ("sync data", 3600)]

    def test_ignores_non_interval_lines(self):
        tree = """## active
- task without interval
  - subtask
## done
"""
        assert _parse_interval_tasks(tree) == []

    def test_handles_seconds_minutes_hours(self):
        tree = """## active
- a [interval: 30s]
- b [interval: 5m]
- c [interval: 2h]
"""
        result = _parse_interval_tasks(tree)
        assert result == [("a", 30), ("b", 300), ("c", 7200)]

    def test_empty_active_section(self):
        tree = """## active
## done
- x [interval: 1m]
"""
        assert _parse_interval_tasks(tree) == []

    def test_case_insensitive_interval_keyword(self):
        tree = """## active
- task [Interval: 30m]
- other [INTERVAL: 1h]
"""
        result = _parse_interval_tasks(tree)
        assert result == [("task", 1800), ("other", 3600)]

    def test_malformed_interval_value(self):
        tree = """## active
- bad [interval: abc]
- no_val [interval: ]
- zero [interval: 0m]
"""
        result = _parse_interval_tasks(tree)
        # "abc", empty value, and 0s interval all filtered out
        assert result == []

    def test_trailing_whitespace_in_line(self):
        tree = """## active
- task [interval: 30m]
"""
        result = _parse_interval_tasks(tree)
        assert result == [("task", 1800)]

    def test_active_section_with_subtasks_and_mixed_content(self):
        tree = """## active
- main task [interval: 30m]
  - subtask not interval
- standalone [interval: 1h]
  - subtask also not interval
## done
"""
        result = _parse_interval_tasks(tree)
        assert result == [("main task", 1800), ("standalone", 3600)]

    def test_ignores_interval_tasks_outside_active(self):
        tree = """## backlog
- old [interval: 30m]
## active
- current [interval: 1h]
"""
        assert _parse_interval_tasks(tree) == [("current", 3600)]

    def test_special_chars_in_task_name(self):
        tree = """## active
- deploy:prod [interval: 30m]
- sync/data [interval: 1h]
- a.b_c [interval: 5m]
"""
        result = _parse_interval_tasks(tree)
        assert result == [("deploy:prod", 1800), ("sync/data", 3600), ("a.b_c", 300)]


# =========================================================================
# _format_duration
# =========================================================================


class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        assert _format_duration(300) == "5m"
        assert _format_duration(3600) == "1h"

    def test_non_even_values(self):
        assert _format_duration(90) == "90s"
        assert _format_duration(3700) == "3700s"

    def test_edge_boundaries(self):
        assert _format_duration(0) == "0s"
        assert _format_duration(59) == "59s"      # just under 1m
        assert _format_duration(60) == "1m"        # exactly 1m
        assert _format_duration(3599) == "3599s"   # just under 1h
        assert _format_duration(3600) == "1h"      # exactly 1h

    def test_large_values(self):
        assert _format_duration(86400) == "24h"
        assert _format_duration(7200) == "2h"
        assert _format_duration(120) == "2m"


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
        assert _is_heartbeat_ok("HEARTBEAT_OK_THING") is True   # starts with HEARTBEAT_OK
        assert _is_heartbeat_ok("XHEARTBEAT_OK") is False        # doesn't start with it

    def test_leading_whitespace(self):
        assert _is_heartbeat_ok("  HEARTBEAT_OK") is True

    def test_whitespace_only(self):
        assert _is_heartbeat_ok("   ") is False


# =========================================================================
# _build_prompt
# =========================================================================


class TestBuildPrompt:
    def test_contains_due_tasks(self):
        service = _make_service(agent_loop=MagicMock())
        prompt = service._build_prompt([("check health", 1800), ("sync", 3600)])
        assert "check health" in prompt
        assert "sync" in prompt
        assert _HEARTBEAT_OK in prompt

    def test_single_task_format(self):
        service = _make_service()
        prompt = service._build_prompt([("check", 300)])
        assert "- check (every 5m)" in prompt
        assert _HEARTBEAT_OK in prompt

    def test_ordering_and_section_headers(self):
        service = _make_service()
        prompt = service._build_prompt([("a", 60), ("b", 3600)])
        assert "Heartbeat" in prompt.split("\n")[0]
        assert "Reply HEARTBEAT_OK" in prompt
        assert "- a (every 1m)" in prompt
        assert "- b (every 1h)" in prompt


# =========================================================================
# Skip chain
# =========================================================================


class TestSkipChain:
    async def test_skips_when_disabled(self):
        service = _make_service(enabled=False)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0  # not updated

    async def test_skips_when_no_tree_md(self, tmp_path):
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0  # cooldown not triggered

    async def test_skips_when_session_busy(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        loop._session_dispatch = {"cli:direct": MagicMock()}
        service = _make_service(agent_loop=loop)
        service._last_run = 0.0
        await service._tick()
        assert service._last_run == 0.0

    async def test_fires_when_tasks_due(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))
        service = _make_service(agent_loop=loop)
        service._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        await service._tick()
        loop.process_direct.assert_awaited_once()
        # timestamp updated after run
        assert service._state.last_run("check health") is not None

    async def test_skips_when_not_due(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        service._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        service._state.mark_run("check health", ts=9999999999.0)
        await service._tick()
        loop.process_direct.assert_not_called()

    async def test_skips_when_state_not_initialized(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        service = _make_service(agent_loop=loop)
        service._state = None
        await service._tick()
        loop.process_direct.assert_not_called()

    async def test_updates_last_run_on_fire(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))
        service = _make_service(agent_loop=loop)
        service._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        service._last_run = 0.0
        await service._tick()
        assert service._last_run > 0.0


# =========================================================================
# HEARTBEAT_OK response handling
# =========================================================================


class TestHeartbeatOkResponse:
    async def test_sets_content_to_none_on_heartbeat_ok(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        response = MagicMock(content="HEARTBEAT_OK all good")
        loop.process_direct = AsyncMock(return_value=response)
        service = _make_service(agent_loop=loop)
        service._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
        await service._tick()
        assert response.content is None

    async def test_keeps_content_on_normal_response(self, tmp_path):
        _write_tree(tmp_path, ["check health", "30m"])
        loop = _make_loop(tmp_path)
        response = MagicMock(content="Found an issue, fixed it")
        loop.process_direct = AsyncMock(return_value=response)
        service = _make_service(agent_loop=loop)
        service._state = HeartbeatState(tmp_path / "tasks" / ".heartbeat_state.json")
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


def _make_service(
    agent_loop=None,
    enabled: bool = True,
) -> HeartbeatService:
    return HeartbeatService(
        agent_loop=agent_loop or MagicMock(),
        enabled=enabled,
    )


def _make_loop(tmp_path: Path) -> MagicMock:
    loop = MagicMock()
    loop.workspace = tmp_path
    loop._session_dispatch = {}
    loop.dispatch_manager = MagicMock()
    return loop


def _write_tree(tmp_path: Path, task: list[str]) -> Path:
    """Write a minimal TREE.md with one interval task under ## active."""
    tree_dir = tmp_path / "tasks"
    tree_dir.mkdir(parents=True, exist_ok=True)
    tree_path = tree_dir / "TREE.md"
    name, interval = task
    tree_path.write_text(
        f"## active\n- {name} [interval: {interval}]\n## done\n",
        encoding="utf-8",
    )
    return tree_path
