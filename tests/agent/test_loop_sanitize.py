"""Tests for AgentLoop _sanitize_persisted_blocks and _append_turn_to_session."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.utils.media_decode import image_placeholder_text


def _make_light_loop(**attrs) -> MagicMock:
    """Create a lightweight mock AgentLoop with method bound from the real class."""
    loop = MagicMock(spec=AgentLoop)
    loop.max_tool_result_chars = attrs.get("max_tool_result_chars", 100)
    return loop


class TestSanitizePersistedBlocks:
    def test_image_url_replaced_with_placeholder(self):
        loop = _make_light_loop()
        blocks = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "img.png"}},
        ]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks)
        assert result[0]["type"] == "text"
        assert result[0]["text"] == image_placeholder_text("img.png")

    def test_runtime_context_dropped(self):
        loop = _make_light_loop()
        blocks = [
            {"type": "text", "text": ContextBuilder._RUNTIME_CONTEXT_TAG + "\n...stuff..."},
            {"type": "text", "text": "keep me"},
        ]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks, drop_runtime=True)
        assert len(result) == 1
        assert result[0]["text"] == "keep me"

    def test_runtime_context_kept_when_not_dropping(self):
        loop = _make_light_loop()
        tag = ContextBuilder._RUNTIME_CONTEXT_TAG
        blocks = [{"type": "text", "text": tag + "\nstuff"}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks, drop_runtime=False)
        assert len(result) == 1

    def test_text_truncation(self):
        loop = _make_light_loop(max_tool_result_chars=20)
        long_text = "x" * 200
        blocks = [{"type": "text", "text": long_text}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks, should_truncate_text=True)
        # truncate_text adds "\n... (truncated)" suffix
        assert result[0]["text"].endswith("... (truncated)")

    def test_text_not_truncated_when_under_limit(self):
        loop = _make_light_loop(max_tool_result_chars=100)
        short_text = "hello"
        blocks = [{"type": "text", "text": short_text}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks, should_truncate_text=True)
        assert result[0]["text"] == "hello"

    def test_text_not_truncated_when_flag_false(self):
        loop = _make_light_loop(max_tool_result_chars=100)
        long_text = "x" * 200
        blocks = [{"type": "text", "text": long_text}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks, should_truncate_text=False)
        assert len(result[0]["text"]) == 200

    def test_non_dict_passthrough(self):
        loop = _make_light_loop()
        blocks = ["just a string", 42, {"type": "text", "text": "ok"}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks)
        assert len(result) == 3
        assert result[0] == "just a string"

    def test_non_matching_blocks_passthrough(self):
        loop = _make_light_loop()
        blocks = [{"type": "tool_use", "name": "read_file_tool"}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks)
        assert len(result) == 1
        assert result[0]["type"] == "tool_use"

    def test_image_url_no_meta(self):
        loop = _make_light_loop()
        blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
        result = AgentLoop._sanitize_persisted_blocks(loop, blocks)
        assert result[0]["text"] == "[image]"  # image_placeholder_text(None) → "[image]"


class TestAppendTurnToSession:
    def test_appends_messages_after_skip(self):
        loop = _make_light_loop()
        session = MagicMock()
        session.messages = []
        msgs = [
            {"role": "user", "content": "skip me"},
            {"role": "assistant", "content": "hello"},
        ]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=1)
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "hello"

    def test_skips_empty_assistant(self):
        loop = _make_light_loop()
        session = MagicMock()
        session.messages = []
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "assistant", "content": "real"},
        ]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=0)
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "real"

    def test_truncates_tool_result_content(self):
        loop = _make_light_loop(max_tool_result_chars=20)
        session = MagicMock()
        session.messages = []
        long_content = "x" * 200
        msgs = [{"role": "tool", "content": long_content}]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=0)
        assert len(session.messages) == 1
        assert session.messages[0]["content"].endswith("... (truncated)")

    def test_strips_runtime_context_from_user(self):
        loop = _make_light_loop()
        session = MagicMock()
        session.messages = []
        tag = ContextBuilder._RUNTIME_CONTEXT_TAG
        end = ContextBuilder._RUNTIME_CONTEXT_END
        user_msg = f"{tag}\nnoise\n{end}\n--- latest user message below ---\nreal content"
        msgs = [{"role": "user", "content": user_msg}]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=0)
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "real content"

    def test_strips_runtime_context_no_end_marker(self):
        loop = _make_light_loop()
        session = MagicMock()
        session.messages = []
        tag = ContextBuilder._RUNTIME_CONTEXT_TAG
        user_msg = f"{tag}\nstuff\nstill here"
        msgs = [{"role": "user", "content": user_msg}]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=0)
        content = session.messages[0]["content"]
        assert tag not in content

    def test_adds_timestamp_and_updates_updated_at(self):
        loop = _make_light_loop()
        session = MagicMock()
        session.messages = []
        msgs = [{"role": "assistant", "content": "hi"}]
        AgentLoop._append_turn_to_session(loop, session, msgs, skip=0)
        assert "timestamp" in session.messages[0]
        assert session.updated_at is not None
