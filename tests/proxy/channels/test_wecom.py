"""Tests for WecomProxyChannel — _chat_frames eviction."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

from nanobot.proxy.channels.wecom import WecomProxyChannel


def _make_frame(chat_id: str) -> MagicMock:
    """Create a minimal mock frame for testing."""
    frame = MagicMock()
    frame.body = {
        "msgid": f"msg_{chat_id}",
        "chatid": chat_id,
        "sendertime": "123",
        "from": {"userid": "user"},
        "text": {"content": "hello"},
    }
    return frame


class TestChatFramesEviction:
    def test_frames_grow_up_to_limit(self):
        """_chat_frames grows normally until the 2000 limit."""
        channel = WecomProxyChannel(
            config={"bot_id": "test", "secret": "test"},
            hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
            channel="wecom", bot="test",
        )
        channel.check_duplicate = MagicMock(return_value=False)
        channel.build_message = MagicMock(return_value={})
        channel.send_to_hub = MagicMock()

        for i in range(1500):
            frame = _make_frame(f"chat_{i}")
            channel._process_message(frame, "text")

        assert len(channel._chat_frames) == 1500

    def test_frames_evicted_when_over_limit(self):
        """Oldest frames are evicted when _chat_frames exceeds 2000."""
        channel = WecomProxyChannel(
            config={"bot_id": "test", "secret": "test"},
            hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
            channel="wecom", bot="test",
        )
        channel.check_duplicate = MagicMock(return_value=False)
        channel.build_message = MagicMock(return_value={})
        channel.send_to_hub = MagicMock()

        for i in range(2500):
            frame = _make_frame(f"chat_{i}")
            channel._process_message(frame, "text")

        assert len(channel._chat_frames) == 2000

    def test_oldest_entry_evicted_first(self):
        """The very first chat_id should be gone after 2001 inserts."""
        channel = WecomProxyChannel(
            config={"bot_id": "test", "secret": "test"},
            hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
            channel="wecom", bot="test",
        )
        channel.check_duplicate = MagicMock(return_value=False)
        channel.build_message = MagicMock(return_value={})
        channel.send_to_hub = MagicMock()

        frame0 = _make_frame("chat_0")
        channel._process_message(frame0, "text")
        assert "chat_0" in channel._chat_frames

        for i in range(1, 2002):
            frame = _make_frame(f"chat_{i}")
            channel._process_message(frame, "text")

        assert "chat_0" not in channel._chat_frames
