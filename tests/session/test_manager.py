"""Tests for SessionManager and Session (DB-only, no JSONL path)."""

from __future__ import annotations

from unittest.mock import MagicMock

from nanobot.session.manager import (
    Session,
    SessionManager,
    find_legal_message_start,
)


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

    def test_format_timestamp_utc(self):
        result = Session._format_timestamp("2026-01-01T00:00:00Z")
        assert result == "2026-01-01 00:00:00 UTC"

    def test_format_timestamp_with_timezone(self):
        result = Session._format_timestamp("2026-01-01T00:00:00Z", timezone="Asia/Shanghai")
        assert result == "2026-01-01 08:00:00 CST"

    def test_format_timestamp_no_timestamp(self):
        result = Session._format_timestamp("")
        assert result is None

    def test_format_timestamp_none(self):
        result = Session._format_timestamp(None)
        assert result is None

    def test_media_breadcrumbs(self):
        s = Session(key="ch:u")
        s.add_message("user", "text", media=["/path/to/img.png"])
        history = s.get_history(max_messages=10)
        assert "[image:" in history[0]["content"]

    def test_max_tokens_recovered_user_turn(self, monkeypatch):
        s = Session(key="ch:u")
        s.add_message("user", "first")
        s.add_message("assistant", "long" * 50)
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 1000,
        )
        history = s.get_history(max_messages=10, max_tokens=50)
        assert any(m["role"] == "user" for m in history)


class TestGetOrCreate:
    def test_uses_db_when_available(self):
        db = MagicMock()
        db.load_session.return_value = Session(key="ch:u", messages=[{"role": "user", "content": "hi"}])
        mgr = SessionManager(db=db)
        s = mgr.get_or_create("ch:u")
        assert s.key == "ch:u"
        assert len(s.messages) == 1

    def test_creates_new_when_db_returns_none(self):
        db = MagicMock()
        db.load_session.return_value = None
        mgr = SessionManager(db=db)
        s = mgr.get_or_create("ch:new")
        assert s.key == "ch:new"
        assert s.messages == []

    def test_returns_cached_session(self):
        mgr = SessionManager()
        s1 = mgr.get_or_create("ch:u")
        s2 = mgr.get_or_create("ch:u")
        assert s1 is s2


class TestSave:
    def test_save_with_db(self):
        db = MagicMock()
        mgr = SessionManager(db=db)
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        mgr.save(s)
        # New session: prev_count=0, current=1 → incremental append
        db.append_messages.assert_called_once_with("ch:u", [s.messages[0]])

    def test_save_with_db_updates_cache(self):
        db = MagicMock()
        mgr = SessionManager(db=db)
        s = Session(key="ch:u")
        mgr.save(s)
        assert mgr._cache.get("ch:u") is s

    def test_save_incremental_appends(self):
        """New-message saves call append_messages; full-save used after removal."""
        db = MagicMock()
        mgr = SessionManager(db=db)
        s = Session(key="ch:u")
        s.add_message("user", "first")
        # Save a new session: prev_count=0, current=1 → append
        mgr.save(s)
        db.append_messages.assert_called_once()

        s.add_message("user", "second")
        # Second save: incremental (prev_count=1, current=2)
        mgr.save(s)
        assert db.append_messages.call_count == 2

        # Simulate removal (e.g. after summary compression): triggers full save
        s.messages.pop(0)
        mgr.save(s)
        db.save_session.assert_called_once()


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


class TestStripBypassedToolMessages:
    def test_removes_bypassed_prefix(self):
        msgs = [
            {"role": "tool", "content": "[BYPASSED] cancelled", "tool_call_id": "c1"},
            {"role": "user", "content": "hello"},
        ]
        stripped = SessionManager._strip_bypassed_tool_messages(msgs)
        assert len(stripped) == 1
        assert stripped[0]["role"] == "user"

    def test_removes_pending_prefix(self):
        msgs = [
            {"role": "tool", "content": "[PENDING] waiting", "tool_call_id": "c1"},
        ]
        stripped = SessionManager._strip_bypassed_tool_messages(msgs)
        assert len(stripped) == 0

    def test_keeps_normal_tool_messages(self):
        msgs = [
            {"role": "tool", "content": "normal result", "tool_call_id": "c1"},
        ]
        stripped = SessionManager._strip_bypassed_tool_messages(msgs)
        assert len(stripped) == 1

    def test_removes_bypassed_without_timestamp_prefix(self):
        stripped = SessionManager._strip_bypassed_tool_messages([
            {"role": "tool", "content": "[BYPASSED] cancelled", "tool_call_id": "c1"},
        ])
        assert len(stripped) == 0

    def test_handles_backward_compat_timestamp_prefix_with_bypassed(self):
        stripped = SessionManager._strip_bypassed_tool_messages([
            {"role": "tool", "content": "[Message Time: ...]\n[BYPASSED] cancelled", "tool_call_id": "c1"},
        ])
        assert len(stripped) == 0

    def test_handles_non_string_content(self):
        stripped = SessionManager._strip_bypassed_tool_messages([
            {"role": "tool", "content": 42, "tool_call_id": "c1"},
        ])
        assert len(stripped) == 1


class TestDeleteSession:
    def test_delete_with_db(self):
        db = MagicMock()
        mgr = SessionManager(db=db)
        mgr.get_or_create("ch:u")
        result = mgr.delete_session("ch:u")
        assert result is True
        db.delete_session.assert_called_once_with("ch:u")

    def test_delete_without_db_returns_false(self):
        mgr = SessionManager()
        result = mgr.delete_session("nonexistent")
        assert result is False

    def test_invalidate_removes_from_cache(self):
        mgr = SessionManager()
        mgr.get_or_create("ch:u")
        assert "ch:u" in mgr._cache
        mgr.invalidate("ch:u")
        assert "ch:u" not in mgr._cache

    def test_invalidate_nonexistent_key(self):
        mgr = SessionManager()
        mgr.invalidate("nope")  # should not raise


class TestReadSessionFile:
    def test_read_with_db(self):
        db = MagicMock()
        db.load_session.return_value = Session(key="ch:u", messages=[{"role": "user", "content": "hi"}])
        mgr = SessionManager(db=db)
        result = mgr.read_session_file("ch:u")
        assert result is not None
        assert result["key"] == "ch:u"
        assert len(result["messages"]) == 1

    def test_read_with_db_returns_none(self):
        db = MagicMock()
        db.load_session.return_value = None
        mgr = SessionManager(db=db)
        assert mgr.read_session_file("ch:u") is None

    def test_read_without_db_returns_none(self):
        mgr = SessionManager()
        assert mgr.read_session_file("nonexistent") is None


class TestListSessions:
    def test_list_with_db(self):
        db = MagicMock()
        db.list_sessions.return_value = [{"key": "ch:u", "updated_at": "2026-01-01T00:00:00"}]
        mgr = SessionManager(db=db)
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["key"] == "ch:u"

    def test_list_without_db_returns_empty(self):
        mgr = SessionManager()
        assert mgr.list_sessions() == []


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
        assert find_legal_message_start(msgs) == 0

    def test_tool_pair_across_user_message(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "user", "content": "interruption"},
            {"role": "tool", "tool_call_id": "c1", "content": "late result"},
        ]
        start = find_legal_message_start(msgs)
        assert start == 0

    def test_orphan_tool_inner_loop_runs(self):
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
            {"role": "assistant", "tool_calls": [{"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c2", "content": "good"},
        ]
        start = find_legal_message_start(msgs)
        assert start == 1


class TestFormatTimestampEdgeCases:
    def test_no_timestamp_returns_none(self):
        result = Session._format_timestamp("")
        assert result is None

    def test_invalid_timestamp_returns_none(self):
        result = Session._format_timestamp("not-a-date")
        assert result is None

    def test_none_timestamp_returns_none(self):
        result = Session._format_timestamp(None)
        assert result is None


class TestGetHistoryEdgeCases:
    def test_channel_delivery_before_user_turn(self):
        s = Session(key="ch:u")
        s.add_message("assistant", "delivered", _channel_delivery=True)
        s.add_message("user", "reply")
        history = s.get_history(max_messages=10)
        assert len(history) >= 2

    def test_orphan_tool_at_front_shifted(self):
        s = Session(key="ch:u")
        s.add_message("tool", "result", tool_call_id="orphan")
        s.add_message("user", "hi")
        s.add_message("assistant", "hello")
        history = s.get_history(max_messages=10)
        assert len(history) == 2
        assert history[0]["role"] == "user"

    def test_max_tokens_find_legal_start(self, monkeypatch):
        s = Session(key="ch:u")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 10,
        )
        history = s.get_history(max_messages=10, max_tokens=15)
        assert len(history) >= 1


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
        history = s.get_history(max_messages=10)
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
        monkeypatch.setattr(
            "nanobot.session.manager.estimate_message_tokens",
            lambda m: 10,
        )
        history = s.get_history(max_messages=10, max_tokens=15)
        assert len(history) == 3
