"""Tests for HeartbeatState — persistence and task run tracking."""
from __future__ import annotations

import time

from nanobot.heartbeat.state import HeartbeatState


class TestHeartbeatState:
    def test_new_state_is_empty(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        assert state.all == {}

    def test_last_run_returns_none_for_unknown(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        assert state.last_run("nonexistent") is None

    def test_mark_run_persists_timestamp(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("task1", ts=12345.0)
        assert state.last_run("task1") == 12345.0

    def test_mark_run_updates_existing(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("task1", ts=100.0)
        state.mark_run("task1", ts=200.0)
        assert state.last_run("task1") == 200.0

    def test_mark_tasks_batch_update(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_tasks({"a": 1.0, "b": 2.0})
        assert state.last_run("a") == 1.0
        assert state.last_run("b") == 2.0

    def test_mark_run_defaults_to_now(self, tmp_path):
        before = time.time()
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("task1")
        after = time.time()
        ts = state.last_run("task1")
        assert ts is not None
        assert before <= ts <= after

    def test_persists_across_reload(self, tmp_path):
        path = tmp_path / ".heartbeat_state.json"
        state1 = HeartbeatState(path)
        state1.mark_run("task1", ts=42.0)

        state2 = HeartbeatState(path)
        assert state2.last_run("task1") == 42.0

    def test_handles_corrupted_json(self, tmp_path):
        path = tmp_path / ".heartbeat_state.json"
        path.write_text("not valid json", encoding="utf-8")
        state = HeartbeatState(path)
        assert state.all == {}
        # should still be writable after corrupted load
        state.mark_run("task1", ts=1.0)
        assert state.last_run("task1") == 1.0

    def test_all_returns_copy(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("task1", ts=1.0)
        d = state.all
        d["task1"] = 99.0
        assert state.last_run("task1") == 1.0  # not affected by mutation

    def test_mark_tasks_empty_no_effect(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_tasks({})
        assert state.all == {}

    def test_sequential_updates_accumulate(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("a", ts=1.0)
        state.mark_run("b", ts=2.0)
        state.mark_run("c", ts=3.0)
        assert len(state.all) == 3

    def test_overwrite_preserves_other_keys(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_tasks({"a": 1.0, "b": 2.0})
        state.mark_run("a", ts=99.0)
        assert state.last_run("a") == 99.0
        assert state.last_run("b") == 2.0

    def test_round_trip_string_key_name(self, tmp_path):
        state = HeartbeatState(tmp_path / ".heartbeat_state.json")
        state.mark_run("deploy:prod/sync (v2)", ts=42.0)
        state2 = HeartbeatState(tmp_path / ".heartbeat_state.json")
        assert state2.last_run("deploy:prod/sync (v2)") == 42.0
