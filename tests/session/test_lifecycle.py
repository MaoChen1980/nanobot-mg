"""Tests for SessionLifecycle (prepare → finalize orchestration)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.session.lifecycle import SessionLifecycle


@pytest.fixture
def lifecycle():
    sm = MagicMock()
    recovery = MagicMock()
    return SessionLifecycle(sm, recovery)


class TestPrepare:
    def test_prepare_calls_get_or_create_and_recovery(self, lifecycle):
        session = MagicMock()
        lifecycle._sm.get_or_create.return_value = session
        result = lifecycle.prepare("test:key")
        assert result is session
        lifecycle._sm.get_or_create.assert_called_once_with("test:key")
        lifecycle._recovery.restore_and_clear_checkpoint.assert_called_once_with(session)
        lifecycle._recovery.restore_pending_user_turn.assert_called_once_with(session)

    def test_get_or_create_pass_through(self, lifecycle):
        lifecycle.get_or_create("key:1")
        lifecycle._sm.get_or_create.assert_called_once_with("key:1")

    def test_save_pass_through(self, lifecycle):
        session = MagicMock()
        lifecycle.save(session)
        lifecycle._sm.save.assert_called_once_with(session)


class TestPersistUserMessage:
    def test_persists_text_and_media(self, lifecycle):
        session = MagicMock()
        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="c1",
                             content="hello", media=["/tmp/pic.png"])
        result = lifecycle.persist_user_message(session, msg, pending_ask_id=None)
        assert result is True
        session.add_message.assert_called_once()
        args, kwargs = session.add_message.call_args
        assert args[0] == "user"
        assert args[1] == "hello"
        assert kwargs["media"] == ["/tmp/pic.png"]

    def test_returns_false_when_pending_ask(self, lifecycle):
        session = MagicMock()
        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="hello")
        result = lifecycle.persist_user_message(session, msg, pending_ask_id="ask_1")
        assert result is False
        session.add_message.assert_not_called()

    def test_returns_false_when_no_content(self, lifecycle):
        session = MagicMock()
        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="")
        result = lifecycle.persist_user_message(session, msg, pending_ask_id=None)
        assert result is False

    def test_returns_false_when_whitespace_only(self, lifecycle):
        session = MagicMock()
        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="   ")
        result = lifecycle.persist_user_message(session, msg, pending_ask_id=None)
        assert result is False

    def test_stores_message_id_in_extra(self, lifecycle):
        session = MagicMock()
        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="c1",
                             content="hi", metadata={"message_id": "mid_123"})
        result = lifecycle.persist_user_message(session, msg, pending_ask_id=None)
        assert result is True
        assert session.add_message.call_args[1]["_message_id"] == "mid_123"


class TestFinalize:
    def test_finalize_clears_and_saves(self, lifecycle):
        session = MagicMock()
        lifecycle.finalize(session)
        lifecycle._recovery.clear_pending_user_turn.assert_called_once_with(session)
        lifecycle._recovery.clear_runtime_checkpoint.assert_called_once_with(session)
        lifecycle._sm.save.assert_called_once_with(session)

    def test_finalize_ephemeral_clears_runtime_only(self, lifecycle):
        session = MagicMock()
        lifecycle.finalize_ephemeral(session)
        lifecycle._recovery.clear_runtime_checkpoint.assert_called_once_with(session)
        lifecycle._recovery.clear_pending_user_turn.assert_not_called()
        lifecycle._sm.save.assert_called_once_with(session)


class TestCleanupOnError:
    def test_cleanup_saves_when_cleared(self, lifecycle):
        lifecycle._recovery.clear_pending_user_turn.return_value = True
        result = lifecycle.cleanup_on_error("test:key")
        assert result is True
        lifecycle._sm.get_or_create.assert_called_once_with("test:key")
        lifecycle._sm.save.assert_called_once()

    def test_cleanup_returns_false_when_nothing_cleared(self, lifecycle):
        lifecycle._recovery.clear_pending_user_turn.return_value = False
        result = lifecycle.cleanup_on_error("test:key")
        assert result is False
        lifecycle._sm.save.assert_not_called()
