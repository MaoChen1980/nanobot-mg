"""Tests for nanobot.proxy.protocol — ProxyMessage and HubResponse."""

from __future__ import annotations

from datetime import datetime

from nanobot.proxy.protocol import HubResponse, ProxyMessage


class TestProxyMessage:
    def test_minimal_construction(self):
        msg = ProxyMessage(
            channel="feishu",
            bot="nanobot",
            sender_id="ou_123",
            chat_id="oc_456",
            content="hello",
            message_id="msg_001",
        )
        assert msg.channel == "feishu"
        assert msg.media == []
        assert msg.timestamp == ""
        assert msg.metadata == {}

    def test_to_dict(self):
        msg = ProxyMessage(
            channel="slack",
            bot="bot1",
            sender_id="U123",
            chat_id="C456",
            content="test",
            message_id="mid_1",
            media=["img.png"],
            timestamp="2025-01-01T00:00:00",
            metadata={"key": "val"},
        )
        d = msg.to_dict()
        assert d["channel"] == "slack"
        assert d["media"] == ["img.png"]
        assert d["metadata"] == {"key": "val"}
        assert d["message_id"] == "mid_1"

    def test_from_dict_roundtrip(self):
        data = {
            "channel": "dingtalk",
            "bot": "robot1",
            "sender_id": "uid_1",
            "chat_id": "cid_1",
            "content": "ping",
            "message_id": "mid_001",
            "media": ["file.pdf"],
            "timestamp": "2025-06-01T12:00:00",
            "metadata": {"source": "test"},
        }
        msg = ProxyMessage.from_dict(data)
        assert msg.channel == "dingtalk"
        assert msg.bot == "robot1"
        assert msg.media == ["file.pdf"]
        assert msg.metadata == {"source": "test"}
        assert msg.to_dict() == data

    def test_from_dict_minimal(self):
        data = {
            "channel": "telegram",
            "bot": "tbot",
            "sender_id": "123",
            "chat_id": "456",
            "content": "hi",
            "message_id": "mid_99",
        }
        msg = ProxyMessage.from_dict(data)
        assert msg.media == []
        assert msg.timestamp == ""
        assert msg.metadata == {}

    def test_to_inbound_message_with_timestamp(self):
        msg = ProxyMessage(
            channel="discord",
            bot="dbot",
            sender_id="uid",
            chat_id="cid",
            content="hello",
            message_id="mid",
            timestamp="2025-03-15T10:30:00",
        )
        ib = msg.to_inbound_message()
        assert ib.channel == "proxy:discord:dbot"
        assert ib.content == "hello"
        assert isinstance(ib.timestamp, datetime)
        assert ib.timestamp.year == 2025

    def test_to_inbound_message_without_timestamp(self):
        msg = ProxyMessage(
            channel="discord",
            bot="dbot",
            sender_id="uid",
            chat_id="cid",
            content="now",
            message_id="mid",
        )
        ib = msg.to_inbound_message()
        assert ib.channel == "proxy:discord:dbot"
        assert isinstance(ib.timestamp, datetime)


class TestHubResponse:
    def test_minimal_construction(self):
        resp = HubResponse(success=True)
        assert resp.success is True
        assert resp.reply_to == ""
        assert resp.content == ""
        assert resp.media == []
        assert resp.metadata == {}
        assert resp.error == ""

    def test_to_dict(self):
        resp = HubResponse(
            success=True,
            reply_to="mid_1",
            content="ok",
            media=["img.png"],
            metadata={"a": 1},
            error="",
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["reply_to"] == "mid_1"
        assert d["content"] == "ok"
        assert d["media"] == ["img.png"]
        assert d["metadata"] == {"a": 1}

    def test_from_dict_roundtrip(self):
        data = {
            "success": False,
            "reply_to": "mid_err",
            "content": "failed",
            "media": [],
            "metadata": {},
            "error": "timeout",
        }
        resp = HubResponse.from_dict(data)
        assert resp.success is False
        assert resp.error == "timeout"
        assert resp.content == "failed"
        assert resp.media == []
        assert resp.to_dict() == data

    def test_from_dict_empty(self):
        resp = HubResponse.from_dict({})
        assert resp.success is False
        assert resp.reply_to == ""
        assert resp.content == ""
        assert resp.media == []
        assert resp.metadata == {}
        assert resp.error == ""
