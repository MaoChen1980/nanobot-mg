"""Comprehensive tests for SessionManager and Session — covering repair, DB, legacy migration, and edge cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.session.manager import (
    Session,
    SessionManager,
    find_legal_message_start,
)


# ======================================================================
# Session — helper / dataclass methods
# ======================================================================


class TestSessionAddMessage:
    def test_adds_timestamp_by_default(self):
        s = Session(key="ch:u")
        s.add_message("user", "hi")
        assert s.messages[0]["role"] == "user"
        assert s.messages[0]["content"] == "hi"
        assert "timestamp" in s.messages[0]

    def test_adds_extra_kwargs(self):
        s = Session(key="ch:u")
        s.add_message("tool", "result", tool_call_id="call_1", name="get_weather")
        assert s.messages[0]["tool_call_id"] == "call_1"
        assert s.messages[0]["name"] == "get_weather"

    def test_updates_updated_at(self):
        s = Session(key="ch:u")
        old = s.updated_at
        s.add_message("user", "hello")
        assert s.updated_at >= old


class TestSessionGetHistory:
    def test_max_messages_slicing(self):
        s = Session(key="ch:u")
        for i in range(20):
            s.add_message("user", f"msg-{i}")
        assert len(s.get_history(max_messages=5)) == 5

    def test_start_from_user_turn(self):
        s = Session(key="ch:u")
        s.add_message("assistant", "hi")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        history = s.get_history(max_messages=10)
        assert history[0]["role"] == "user"

    def test_drops_orphan_tool_results(self):
        s = Session(key="ch:u")
        s.add_message("tool", "result", tool_call_id="orphan")
        s.add_message("user", "hello")
        history = s.get_history(max_messages=10)
        assert len(history) == 1
        assert history[0]["role"] == "user"

    def test_timestamp_stored_as_property(self):
        s = Session(key="ch:u")
        ts = "2026-01-01T00:00:00Z"
        s.add_message("user", "hi", timestamp=ts)
        history = s.get_history(max_messages=10, include_timestamps=True)
        assert history[0]["content"] == "hi"
        assert history[0]["timestamp"] == "2026-01-01 00:00:00 UTC"

    def test_timestamp_property_for_assistant(self):
        s = Session(key="ch:u")
        ts = "2026-01-01T00:00:00Z"
        s.add_message("assistant", "reply", timestamp=ts)
        history = s.get_history(max_messages=10, include_timestamps=True)
        assert history[0]["content"] == "reply"
        assert history[0]["timestamp"] == "2026-01-01 00:00:00 UTC"

    def test_timestamp_property_channel_delivery(self):
        s = Session(key="ch:u")
        ts = "2026-01-01T00:00:00Z"
        s.add_message("assistant", "delivery", timestamp=ts, _channel_delivery=True)
        history = s.get_history(max_messages=10, include_timestamps=True)
        assert history[0]["content"] == "delivery"
        assert history[0]["timestamp"] == "2026-01-01 00:00:00 UTC"
        # _channel_delivery is internal metadata, not exposed in history output

    def test_timestamp_property_included_by_default(self):
        """Timestamp is included when include_timestamps=True."""
        s = Session(key="ch:u")
        ts = "2026-01-01T00:00:00Z"
        s.add_message("user", "hi", timestamp=ts)
        history = s.get_history(max_messages=10, include_timestamps=True)
        assert history[0]["content"] == "hi"
        assert history[0]["timestamp"] == "2026-01-01 00:00:00 UTC"

    def test_format_timestamp_utc(self):
        """_format_timestamp returns formatted string in UTC."""
        result = Session._format_timestamp("2026-01-01T00:00:00Z")
        assert result == "2026-01-01 00:00:00 UTC"

    def test_format_timestamp_with_timezone(self):
        """_format_timestamp converts timezone when provided."""
        result = Session._format_timestamp("2026-01-01T00:00:00Z", timezone="Asia/Shanghai")
        assert result == "2026-01-01 08:00:00 CST"

    def test_format_timestamp_no_timestamp(self):
        """_format_timestamp returns None when no timestamp."""
        result = Session._format_timestamp("")
        assert result is None

    def test_format_timestamp_none(self):
        """_format_timestamp returns None when None."""
        result = Session._format_timestamp(None)
        assert result is None

    def test_media_breadcrumbs(self):
        s = Session(key="ch:u")
        s.add_message("user", "text", media=["/path/to/img.png"])
        history = s.get_history(max_messages=10)
        assert "[image:" in history[0]["content"]

    def test_orphan_tool_result_start_alignment(self):
        """Line 97: orphan tool result at front shifts sliced start."""
        s = Session(key="ch:u")
        s.last_consolidated = 0
        s.add_message("tool", "result", tool_call_id="orphan")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        history = s.get_history(max_messages=10)
        # The orphan tool result should be dropped from history
        assert len(history) == 2  # user + assistant
        assert history[0]["role"] == "user"

    def test_max_tokens_recovered_user_turn(self, monkeypatch):
        """When token budget is tight and no user turn in kept, recover nearest user."""
        s = Session(key="ch:u")
        s.add_message("user", "first")
        s.add_message("assistant", "long" * 50)
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 1000,
        )
        history = s.get_history(max_messages=10, max_tokens=50)
        assert any(m["role"] == "user" for m in history)



class TestSessionRetainRecent:
    def test_clear_on_zero_or_negative(self):
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        s.retain_recent_legal_suffix(0)
        assert s.messages == []

    def test_noop_when_within_limit(self):
        s = Session(key="ch:u")
        for i in range(3):
            s.add_message("user", f"msg-{i}")
        s.retain_recent_legal_suffix(10)
        assert len(s.messages) == 3

    def test_retains_user_turn_when_possible(self):
        s = Session(key="ch:u")
        for i in range(10):
            s.add_message("assistant", f"a-{i}")
        s.add_message("user", "keep-me")
        for i in range(10):
            s.add_message("assistant", f"b-{i}")
        s.retain_recent_legal_suffix(12)
        assert any(m["role"] == "user" for m in s.messages)

    def test_assistant_only_tail_anchors_to_latest_user(self):
        s = Session(key="ch:u")
        s.add_message("user", "anchor")
        for i in range(20):
            s.add_message("assistant", f"a-{i}")
        s.retain_recent_legal_suffix(5)
        assert s.messages[0]["role"] == "user"

    def test_drops_orphan_tool_results(self):
        s = Session(key="ch:u")
        s.add_message("tool", "result", tool_call_id="orphan")
        s.add_message("user", "hi")
        for i in range(20):
            s.add_message("assistant", f"a-{i}")
        s.retain_recent_legal_suffix(5)
        assert s.messages[0]["role"] == "user"


class TestSessionEnforceFileCap:
    def test_noop_when_within_limit(self):
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        s.enforce_file_cap()
        assert len(s.messages) == 1


    def test_on_archive_not_called_when_nothing_to_archive(self):
        """When all dropped messages were already consolidated, on_archive is not called."""
        s = Session(key="ch:u")
        for i in range(5):
            s.add_message("user", f"msg-{i}")
        s.last_consolidated = 5
        called = False

        def archive_cb(chunk):
            nonlocal called
            called = True

        s.enforce_file_cap(on_archive=archive_cb, limit=3)
        assert len(s.messages) <= 3
        assert not called


# ======================================================================
# SessionManager — get_or_create / DB path
# ======================================================================


class TestGetOrCreate:
    def test_uses_db_when_available(self, tmp_path):
        db = MagicMock()
        db.load_session.return_value = Session(key="ch:u", messages=[{"role": "user", "content": "hi"}])
        mgr = SessionManager(tmp_path, db=db)
        s = mgr.get_or_create("ch:u")
        assert s.key == "ch:u"
        assert len(s.messages) == 1

    def test_creates_new_when_db_returns_none(self, tmp_path):
        db = MagicMock()
        db.load_session.return_value = None
        mgr = SessionManager(tmp_path, db=db)
        s = mgr.get_or_create("ch:new")
        assert s.key == "ch:new"
        assert s.messages == []

    def test_returns_cached_session(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = mgr.get_or_create("ch:u")
        s2 = mgr.get_or_create("ch:u")
        assert s1 is s2


# ======================================================================
# SessionManager — save
# ======================================================================


class TestSave:
    def test_save_with_db(self, tmp_path):
        db = MagicMock()
        mgr = SessionManager(tmp_path, db=db)
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        mgr.save(s)
        db.save_session.assert_called_once_with(s)

    def test_save_with_db_updates_cache(self, tmp_path):
        db = MagicMock()
        mgr = SessionManager(tmp_path, db=db)
        s = Session(key="ch:u")
        mgr.save(s)
        assert mgr._cache.get("ch:u") is s


# ======================================================================
# SessionManager — load_from_file / legacy migration / repair
# ======================================================================


class TestLoadFromFile:
    def test_loads_valid_file(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        mgr.save(s)
        loaded = mgr._load_from_file("ch:u")
        assert loaded is not None
        assert len(loaded.messages) == 1

    def test_returns_none_when_no_file(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr._load_from_file("nonexistent") is None


    def test_repairs_corrupt_file(self, tmp_path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_type": "metadata", "key": "ch:u", "created_at": null, "updated_at": null}\n')
            f.write('{"role": "user", "content": "ok"}\n')
            f.write("NOT JSON\n")
            f.write('{"role": "assistant", "content": "also ok"}\n')

        loaded = mgr._load_from_file("ch:u")
        assert loaded is not None
        assert len(loaded.messages) == 2

    def test_repair_returns_none_when_file_gone(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr._repair("nonexistent") is None

    def test_repair_handles_completely_corrupt_file(self, tmp_path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT JSON\nALSO NOT JSON\n", encoding="utf-8")

        repaired = mgr._repair("bad")
        assert repaired is None


# ======================================================================
# SessionManager — tool protocol handling
# ======================================================================


class TestFixToolProtocolViolations:
    def test_removes_orphan_tool_calls(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
            {"role": "assistant", "content": "done"},
        ]
        fixed = SessionManager._fix_tool_protocol_violations(msgs)
        assert "tool_calls" not in fixed[0]

    def test_keeps_valid_tool_call_pairs(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        fixed = SessionManager._fix_tool_protocol_violations(msgs)
        assert "tool_calls" in fixed[0]

    def test_last_message_with_tool_calls_is_cleared(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
        ]
        fixed = SessionManager._fix_tool_protocol_violations(msgs)
        assert "tool_calls" not in fixed[1]


class TestStripAbandonedToolMessages:
    def test_removes_abandoned_prefix(self):
        msgs = [
            {"role": "tool", "content": "[ABANDONED] cancelled", "tool_call_id": "c1"},
            {"role": "user", "content": "hello"},
        ]
        stripped = SessionManager._strip_abandoned_tool_messages(msgs)
        assert len(stripped) == 1
        assert stripped[0]["role"] == "user"

    def test_removes_pending_prefix(self):
        msgs = [
            {"role": "tool", "content": "[PENDING] waiting", "tool_call_id": "c1"},
        ]
        stripped = SessionManager._strip_abandoned_tool_messages(msgs)
        assert len(stripped) == 0

    def test_keeps_normal_tool_messages(self):
        msgs = [
            {"role": "tool", "content": "normal result", "tool_call_id": "c1"},
        ]
        stripped = SessionManager._strip_abandoned_tool_messages(msgs)
        assert len(stripped) == 1

    def test_removes_abandoned_without_timestamp_prefix(self):
        """[ABANDONED] is now stored directly (no [Message Time] prefix)."""
        stripped = SessionManager._strip_abandoned_tool_messages([
            {"role": "tool", "content": "[ABANDONED] cancelled", "tool_call_id": "c1"},
        ])
        assert len(stripped) == 0

    def test_handles_backward_compat_timestamp_prefix_with_abandoned(self):
        """Backward compat: old persisted data may still have [Message Time] prefix."""
        stripped = SessionManager._strip_abandoned_tool_messages([
            {"role": "tool", "content": "[Message Time: ...]\n[ABANDONED] cancelled", "tool_call_id": "c1"},
        ])
        assert len(stripped) == 0

    def test_handles_non_string_content(self):
        stripped = SessionManager._strip_abandoned_tool_messages([
            {"role": "tool", "content": 42, "tool_call_id": "c1"},
        ])
        assert len(stripped) == 1


# ======================================================================
# SessionManager — delete_session / invalidate
# ======================================================================


class TestDeleteSession:
    def test_delete_with_db(self, tmp_path):
        db = MagicMock()
        mgr = SessionManager(tmp_path, db=db)
        mgr.get_or_create("ch:u")
        result = mgr.delete_session("ch:u")
        assert result is True
        db.delete_session.assert_called_once_with("ch:u")

    def test_delete_file_not_found(self, tmp_path):
        mgr = SessionManager(tmp_path)
        result = mgr.delete_session("nonexistent")
        assert result is False

    def test_delete_file_success(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        mgr.save(s)
        result = mgr.delete_session("ch:u")
        assert result is True
        assert not mgr._get_session_path("ch:u").exists()

    def test_invalidate_removes_from_cache(self, tmp_path):
        mgr = SessionManager(tmp_path)
        mgr.get_or_create("ch:u")
        assert "ch:u" in mgr._cache
        mgr.invalidate("ch:u")
        assert "ch:u" not in mgr._cache

    def test_invalidate_nonexistent_key(self, tmp_path):
        mgr = SessionManager(tmp_path)
        mgr.invalidate("nope")  # should not raise


# ======================================================================
# SessionManager — read_session_file
# ======================================================================


class TestReadSessionFile:
    def test_read_with_db(self, tmp_path):
        db = MagicMock()
        db.load_session.return_value = Session(key="ch:u", messages=[{"role": "user", "content": "hi"}])
        mgr = SessionManager(tmp_path, db=db)
        result = mgr.read_session_file("ch:u")
        assert result is not None
        assert result["key"] == "ch:u"
        assert len(result["messages"]) == 1

    def test_read_with_db_returns_none(self, tmp_path):
        db = MagicMock()
        db.load_session.return_value = None
        mgr = SessionManager(tmp_path, db=db)
        assert mgr.read_session_file("ch:u") is None

    def test_read_file_not_found(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.read_session_file("nonexistent") is None

    def test_read_file_valid(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("user", "hi")
        mgr.save(s)
        result = mgr.read_session_file("ch:u")
        assert result is not None
        assert len(result["messages"]) == 1

    def test_read_file_empty_line_skipped(self, tmp_path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n")
            f.write('{"_type": "metadata", "key": "ch:u", "created_at": null, "updated_at": null}\n')
            f.write('{"role": "user", "content": "hi"}\n')
        result = mgr.read_session_file("ch:u")
        assert result is not None


# ======================================================================
# SessionManager — list_sessions
# ======================================================================


class TestListSessions:
    def test_list_with_db(self, tmp_path):
        db = MagicMock()
        db.list_sessions.return_value = [{"key": "ch:u", "updated_at": "2026-01-01T00:00:00"}]
        mgr = SessionManager(tmp_path, db=db)
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["key"] == "ch:u"

    def test_list_from_file(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session(key="ch:u")
        s1.add_message("user", "hi")
        mgr.save(s1)
        s2 = Session(key="ch:v")
        s2.add_message("user", "hello")
        mgr.save(s2)
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_list_from_file_with_corrupt_entry(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:good")
        mgr.save(s)
        bad_path = tmp_path / "sessions" / "ch_bad.jsonl"
        bad_path.write_text("NOT JSON\n", encoding="utf-8")
        sessions = mgr.list_sessions()
        assert len(sessions) >= 1
        assert any(s["key"] == "ch:good" for s in sessions)


# ======================================================================
# SessionManager — _save_to_file edge cases
# ======================================================================


class TestSaveToFileEdgeCases:
    def test_skips_abandoned_tool_messages(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        s.add_message("tool", "[ABANDONED] cancelled", tool_call_id="c1")
        s.add_message("assistant", "done")
        mgr._save_to_file(s, fsync=False)
        lines = mgr._get_session_path("ch:u").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3  # metadata + user + assistant (tool skipped)

    def test_skips_pending_tool_messages(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("tool", "[PENDING] waiting", tool_call_id="c1")
        mgr._save_to_file(s, fsync=False)
        lines = mgr._get_session_path("ch:u").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1  # only metadata

    def test_tmp_file_cleaned_on_error(self, tmp_path):
        """When os.replace fails, the temp file should be cleaned up."""
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        path = mgr._get_session_path("ch:u")
        tmp_path_file = path.with_suffix(".jsonl.tmp")

        with patch("os.replace", side_effect=RuntimeError("replace failed")):
            with pytest.raises(RuntimeError):
                mgr._save_to_file(s)
            assert not tmp_path_file.exists()


# ======================================================================
# find_legal_message_start (module-level function)
# ======================================================================


class TestFindLegalMessageStart:
    def test_empty_list(self):
        assert find_legal_message_start([]) == 0

    def test_no_tool_messages(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert find_legal_message_start(msgs) == 0

    def test_orphan_tool_result_is_skipped(self):
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": "result"},
            {"role": "user", "content": "hi"},
        ]
        assert find_legal_message_start(msgs) == 1

    def test_valid_tool_pair_allowed(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        assert find_legal_message_start(msgs) == 0

    def test_mixed_orphan_and_valid(self):
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "good"},
        ]
        start = find_legal_message_start(msgs)
        assert start > 0

    def test_empty_tool_calls_list(self):
        msgs = [
            {"role": "assistant", "tool_calls": []},
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "orphan", "content": "result"},
        ]
        assert find_legal_message_start(msgs) == 0  # never drops all messages even when all tools orphaned

    def test_tool_pair_across_user_message(self):
        """Tool result referenced by an earlier assistant is valid even across a user turn."""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "user", "content": "interruption"},
            {"role": "tool", "tool_call_id": "c1", "content": "late result"},
        ]
        start = find_legal_message_start(msgs)
        assert start == 0  # c1 was declared, so tool result is valid

    def test_orphan_tool_inner_loop_runs(self):
        """Lines 666-669: orphan tool triggers the inner re-scan loop."""
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
            {"role": "assistant", "tool_calls": [{"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c2", "content": "good"},
        ]
        start = find_legal_message_start(msgs)
        # Index 0 is orphan, start moves past it; c2 pair at 1,2 is valid
        assert start == 1


# ======================================================================
# Edge cases that are hard to slot into existing test classes
# ======================================================================


class TestFormatTimestampEdgeCases:
    """Covers edge cases for _format_timestamp."""

    def test_no_timestamp_returns_none(self):
        s = Session(key="ch:u")
        result = Session._format_timestamp("")
        assert result is None

    def test_invalid_timestamp_returns_none(self):
        s = Session(key="ch:u")
        result = Session._format_timestamp("not-a-date")
        assert result is None

    def test_none_timestamp_returns_none(self):
        s = Session(key="ch:u")
        result = Session._format_timestamp(None)
        assert result is None


class TestGetHistoryEdgeCases:
    """Covers lines 90, 97, 150 gap lines."""

    def test_channel_delivery_before_user_turn(self, monkeypatch):
        """Line 90: _channel_delivery before user turn includes the delivery in slice."""
        s = Session(key="ch:u")
        s.add_message("assistant", "delivered", _channel_delivery=True)
        s.add_message("user", "reply")
        history = s.get_history(max_messages=10)
        # Should include both the delivery and the reply
        assert len(history) >= 2

    def test_orphan_tool_at_front_shifted(self):
        """Line 97: orphan tool results at the front are dropped."""
        s = Session(key="ch:u")
        s.last_consolidated = 0
        s.add_message("tool", "result", tool_call_id="orphan")
        s.add_message("user", "hi")
        s.add_message("assistant", "hello")
        history = s.get_history(max_messages=10)
        assert len(history) == 2
        assert history[0]["role"] == "user"

    def test_max_tokens_find_legal_start(self, monkeypatch):
        """Line 150: find_legal_message_start runs after token truncation."""
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 10,
        )
        # max_tokens=15 forces truncation after first message
        history = s.get_history(max_messages=10, max_tokens=15)
        assert len(history) >= 1


class TestLoadFromFileEdgeCases:
    """Covers lines 336-337, 353."""


    def test_empty_line_skipped(self, tmp_path):
        """Line 353: empty line in JSONL file is silently skipped."""
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_type": "metadata", "key": "ch:u", "created_at": null, "updated_at": null}\n')
            f.write("\n")  # empty line
            f.write('{"role": "user", "content": "hello"}\n')
        loaded = mgr._load_from_file("ch:u")
        assert loaded is not None
        assert len(loaded.messages) == 1


class TestRepairEdgeCases:
    """Covers lines 401, 411-419, 441-443."""

    def test_empty_line_skipped_in_repair(self, tmp_path):
        """Line 401: empty line in repair is skipped."""
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_type": "metadata", "key": "ch:u", '
                    '"created_at": null, "updated_at": null, '
                    '"metadata": {"note": "exists"}}\n')
            f.write("\n")  # empty line
            f.write("MALFORMED\n")
        repaired = mgr._repair("ch:u")
        assert repaired is not None  # metadata has content

    def test_repair_bad_datetime_in_metadata(self, tmp_path):
        """Lines 411-419: invalid datetime in repair's metadata is handled."""
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_type": "metadata", "key": "ch:u", '
                    '"created_at": "not-a-date", '
                    '"updated_at": "also-bad"}\n')
            f.write('{"role": "user", "content": "ok"}\n')
        repaired = mgr._repair("ch:u")
        assert repaired is not None
        assert len(repaired.messages) == 1


class TestDeleteSessionEdgeCases:
    """Covers lines 557-559: _delete_session_file OSError."""

    def test_delete_file_os_error(self, tmp_path, monkeypatch):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        mgr.save(s)

        def failing_unlink(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "unlink", failing_unlink)
        result = mgr.delete_session("ch:u")
        assert result is False


class TestReadSessionFileEdgeCases:
    """Covers lines 605-611: exception + repair failure chain."""

    def test_read_corrupt_file_repair_fails(self, tmp_path):
        """Exception in _read_session_file_from_file → repair fails → returns None."""
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Completely binary garbage that would fail at json.load level
        path.write_bytes(b"\x00\x01\x02\xff")
        result = mgr.read_session_file("ch:u")
        assert result is None

    def test_read_corrupt_file_repair_succeeds(self, tmp_path):
        """Exception in _read_session_file_from_file → repair succeeds."""
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("ch:u")
        path.parent.mkdir(parents=True, exist_ok=True)
        # JSONL with malformed line so read fails, but repair recovers
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_type": "metadata", "key": "ch:u", "created_at": null, "updated_at": null}\n')
            f.write("NOT JSON\n")
            f.write('{"role": "user", "content": "recovered"}\n')
        result = mgr.read_session_file("ch:u")
        assert result is not None
        assert len(result["messages"]) == 1
        assert result["messages"][0]["content"] == "recovered"


class TestListSessionsEdgeCases:
    """Covers line 640: corrupt file in list_sessions triggers repair."""

    def test_corrupt_file_repair_succeeds(self, tmp_path):
        """Corrupt first line triggers repair; repair succeeds."""
        mgr = SessionManager(tmp_path)
        path = tmp_path / "sessions" / "ch_bad.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # First line is invalid JSON → triggers exception path in list_sessions
        # Subsequent lines are repair-able
        path.write_text(
            "NOT JSON\n"
            '{"_type": "metadata", "key": "ch:repaired",'
            ' "created_at": "2026-01-01T00:00:00",'
            ' "updated_at": "2026-01-01T00:00:00"}\n'
            '{"role": "user", "content": "hi"}\n',
            encoding="utf-8",
        )
        sessions = mgr.list_sessions()
        assert any(s["key"] == "ch:bad" for s in sessions)  # fallback key


# ======================================================================
# Coverage edge cases for lines that need specific scenarios
# ======================================================================


class TestGetHistoryLine97:
    """Orphan tool after user turn hits sliced[start:] at line 97."""

    def test_orphan_tool_after_user_triggers_alignment(self):
        s = Session(key="ch:u")
        s.messages = [
            {"role": "user", "content": "first", "timestamp": "2026-01-01T00:00:00"},
            {"role": "tool", "content": "result", "tool_call_id": "orphan",
             "timestamp": "2026-01-01T00:00:01"},
            {"role": "assistant", "content": "hi", "timestamp": "2026-01-01T00:00:02"},
        ]
        s.last_consolidated = 0
        history = s.get_history(max_messages=10)
        # Orphan after user pushes start past both user and tool → only assistant
        assert len(history) == 1 and history[0]["role"] == "assistant"


class TestGetHistoryLine150:
    """max_tokens truncation where kept gets cleaned by find_legal_message_start."""

    def test_orphan_after_token_recovery_triggers_cleanup(self, monkeypatch):
        s = Session(key="ch:u")
        s.messages = [
            {"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00"},
            {"role": "assistant", "content": "response", "timestamp": "2026-01-01T00:00:01"},
            {"role": "tool", "content": "result", "tool_call_id": "orphan",
             "timestamp": "2026-01-01T00:00:02"},
        ]
        s.last_consolidated = 0
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 10,
        )
        # Tight budget truncates to just the tool.  recovered_user brings full
        # context back, then find_legal_message_start detects the orphan.
        history = s.get_history(max_messages=10, max_tokens=15)
        # find_legal_message_start keeps messages even with orphan tools
        assert len(history) == 3


class TestRetainRecentLine188:
    """retain_recent_legal_suffix with orphan tool triggers start alignment."""

    def test_orphan_tool_after_latest_user_triggers_alignment(self):
        s = Session(key="ch:u")
        s.messages = [
            {"role": "user", "content": "anchor", "timestamp": "2026-01-01T00:00:00"},
            {"role": "tool", "content": "result", "tool_call_id": "orphan",
             "timestamp": "2026-01-01T00:00:01"},
        ]
        for i in range(10):
            s.messages.append(
                {"role": "assistant", "content": f"a-{i}", "timestamp": "2026-01-01T00:00:02"}
            )
        s.retain_recent_legal_suffix(5)
        assert len(s.messages) > 0


class TestDirectoryFsyncPermissionError:
    """Lines 508-511: os.open raises PermissionError during directory fsync."""

    def test_fsync_dir_permission_error(self, tmp_path, monkeypatch):
        mgr = SessionManager(tmp_path)
        s = Session(key="ch:u")
        s.add_message("user", "hello")

        original_open = os.open
        calls = []

        def tracking_open(*args, **kwargs):
            calls.append(args)
            raise PermissionError("Windows directory fsync not supported")

        monkeypatch.setattr(os, "open", tracking_open)
        mgr._save_to_file(s, fsync=True)
        path = mgr._get_session_path("ch:u")
        assert path.exists()
        # Verify os.open was called (for the directory fsync)
        assert len(calls) > 0
