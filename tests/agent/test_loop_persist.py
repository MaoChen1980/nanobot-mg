"""Tests for AgentLoop _persist_subagent_followup and _init_framework_dir."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage


def _make_session():
    s = MagicMock()
    s.messages = []
    return s


class TestPersistSubagentFollowup:
    def _call(self, session, msg, **loop_attrs):
        loop = MagicMock(spec=AgentLoop)
        for k, v in loop_attrs.items():
            setattr(loop, k, v)
        return AgentLoop._persist_subagent_followup(loop, session, msg)

    def test_empty_content_returns_false(self):
        msg = InboundMessage(channel="cli", sender_id="boss", chat_id="direct", content="", media=[])
        assert not self._call(_make_session(), msg)

    def test_dedup_same_subagent_task_id(self):
        session = _make_session()
        session.messages = [
            {"injected_event": "subagent_result", "subagent_task_id": "task-1"},
        ]
        msg = InboundMessage(
            channel="cli", sender_id="boss", chat_id="direct",
            content="result here", media=[],
            metadata={"subagent_task_id": "task-1", "injected_event": "subagent_result"},
        )
        assert not self._call(session, msg)

    def test_notification_always_appended(self):
        session = _make_session()
        msg = InboundMessage(
            channel="cli", sender_id="boss", chat_id="direct",
            content="notification", media=[],
            metadata={"injected_event": "subagent_request"},
        )
        assert self._call(session, msg)
        session.add_message.assert_called_once()

    def test_adds_correct_fields(self):
        session = _make_session()
        msg = InboundMessage(
            channel="cli", sender_id="boss", chat_id="direct",
            content="sub result", media=[],
            metadata={"subagent_task_id": "task-42", "injected_event": "subagent_result"},
        )
        self._call(session, msg)
        session.add_message.assert_called_once()
        call_kwargs = session.add_message.call_args[1]
        assert call_kwargs["sender_id"] == "boss"
        assert call_kwargs["subagent_task_id"] == "task-42"
        assert call_kwargs["injected_event"] == "subagent_result"


class TestInitFrameworkDir:
    def test_early_return_when_target_exists(self, tmp_path):
        target = tmp_path / "framework"
        target.mkdir()
        AgentLoop._init_framework_dir(tmp_path)
        assert target.exists()

    def test_copies_templates_when_missing(self, tmp_path):
        with (
            patch("importlib.resources.files") as mock_pkg_files,
            patch("shutil.copytree") as mock_copy,
        ):
            mock_src = MagicMock()
            mock_src.is_dir.return_value = True
            mock_pkg_files.return_value = mock_src
            AgentLoop._init_framework_dir(tmp_path)
            mock_copy.assert_called_once()

    def test_noop_when_no_bundled_templates(self, tmp_path):
        with (
            patch("importlib.resources.files") as mock_pkg_files,
            patch("shutil.copytree") as mock_copy,
        ):
            mock_src = MagicMock()
            mock_src.is_dir.return_value = False
            mock_pkg_files.return_value = mock_src
            mock_src.__truediv__.return_value = mock_src
            AgentLoop._init_framework_dir(tmp_path)
            mock_copy.assert_not_called()

    def test_logs_exception_on_error(self, tmp_path):
        with (
            patch("importlib.resources.files", side_effect=Exception("boom")),
            patch("nanobot.agent.loop.logger.exception") as mock_log,
        ):
            AgentLoop._init_framework_dir(tmp_path)
            mock_log.assert_called_once()
