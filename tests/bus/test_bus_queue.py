"""Tests for nanobot.bus.queue — MessageBus and related message types."""

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_publish_and_consume_inbound(self):
        bus = MessageBus()
        msg = InboundMessage(channel="test", sender_id="user1", chat_id="c1", content="hello")
        await bus.publish_inbound(msg)
        consumed = await bus.consume_inbound()
        assert consumed is msg

    @pytest.mark.asyncio
    async def test_publish_and_consume_outbound(self):
        bus = MessageBus()
        msg = OutboundMessage(channel="test", chat_id="c1", content="reply")
        await bus.publish_outbound(msg)
        consumed = await bus.consume_outbound()
        assert consumed is msg

    def test_inbound_size_starts_at_zero(self):
        bus = MessageBus()
        assert bus.inbound_size == 0

    def test_outbound_size_starts_at_zero(self):
        bus = MessageBus()
        assert bus.outbound_size == 0

    @pytest.mark.asyncio
    async def test_inbound_size_tracks_pending(self):
        bus = MessageBus()
        msg = InboundMessage(channel="test", sender_id="u", chat_id="c", content="m")
        await bus.publish_inbound(msg)
        assert bus.inbound_size == 1
        await bus.consume_inbound()
        assert bus.inbound_size == 0

    @pytest.mark.asyncio
    async def test_outbound_size_tracks_pending(self):
        bus = MessageBus()
        msg = OutboundMessage(channel="test", chat_id="c", content="m")
        await bus.publish_outbound(msg)
        assert bus.outbound_size == 1
        await bus.consume_outbound()
        assert bus.outbound_size == 0


class TestInboundMessage:
    def test_session_key_uses_channel_and_chat_id(self):
        msg = InboundMessage(channel="slack", sender_id="u", chat_id="C123", content="hi")
        assert msg.session_key == "slack:C123"

    def test_session_key_override(self):
        msg = InboundMessage(
            channel="slack", sender_id="u", chat_id="C123", content="hi",
            session_key_override="custom:key",
        )
        assert msg.session_key == "custom:key"

    def test_timestamp_is_utc_aware(self):
        from datetime import timezone
        msg = InboundMessage(channel="t", sender_id="u", chat_id="c", content="")
        assert msg.timestamp.tzinfo is not None
        assert msg.timestamp.tzinfo == timezone.utc


class TestOutboundMessage:
    def test_outbound_to_hub_response(self):
        from nanobot.proxy.protocol import outbound_to_hub_response
        msg = OutboundMessage(
            channel="test", chat_id="c1", content="hello",
            media=["f1.txt"], metadata={"key": "val"},
        )
        resp = outbound_to_hub_response(msg, reply_to="parent123")
        assert resp.success is True
        assert resp.content == "hello"
        assert resp.media == ["f1.txt"]
        assert resp.metadata == {"key": "val"}
        assert resp.reply_to == "parent123"
