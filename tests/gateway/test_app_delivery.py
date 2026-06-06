"""Tests for GatewayApplication delivery (outbound routing, proxy, recording)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage
from nanobot.config.schema import Config
from nanobot.gateway.app import GatewayApplication


def _make_mocked_app() -> GatewayApplication:
    app = GatewayApplication(Config())
    app.bus = MagicMock()
    app.bus.publish_outbound = AsyncMock()
    app.session_manager = MagicMock()
    app.session_manager.get_or_create = MagicMock(return_value=MagicMock())
    app.proxy_manager = MagicMock()
    app.proxy_manager.has_proxy = MagicMock(return_value=False)
    app.proxy_manager.deliver_to_proxy = AsyncMock(return_value=True)
    message_tool = MagicMock(spec=MessageTool)
    app.agent = MagicMock()
    app.agent.tools = {"message_tool": message_tool}
    app.cron = MagicMock()
    return app


@pytest.fixture
def app() -> GatewayApplication:
    return _make_mocked_app()


class TestDeliverToChannel:
    @pytest.fixture
    def deliver_fn(self, app):
        app._wire_callbacks()
        return app.agent.tools["message_tool"].set_send_callback.call_args[0][0]

    @pytest.mark.asyncio
    async def test_record_creates_session(self, app, deliver_fn):
        msg = OutboundMessage(channel="telegram", chat_id="u1", content="Hello")
        await deliver_fn(msg, record=True)

        app.session_manager.get_or_create.assert_called_once()
        session = app.session_manager.get_or_create.return_value
        session.add_message.assert_called_once_with("assistant", "Hello", _channel_delivery=True)
        app.session_manager.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_channel_skips_persist(self, app, deliver_fn):
        msg = OutboundMessage(channel="cli", chat_id="direct", content="Hello")
        await deliver_fn(msg, record=True)
        app.session_manager.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_channel_delivery_metadata(self, app, deliver_fn):
        msg = OutboundMessage(
            channel="telegram", chat_id="u1", content="Hi",
            metadata={"_record_channel_delivery": True},
        )
        await deliver_fn(msg)
        app.session_manager.get_or_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_record_no_session(self, app, deliver_fn):
        msg = OutboundMessage(channel="telegram", chat_id="u1", content="No save")
        await deliver_fn(msg)
        app.session_manager.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_delivery_via_bus(self, app, deliver_fn):
        msg = OutboundMessage(channel="telegram", chat_id="u1", content="Direct")
        await deliver_fn(msg)
        app.bus.publish_outbound.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_proxy_delivery(self, app, deliver_fn):
        app.proxy_manager.has_proxy.side_effect = lambda ch: ch in ("proxy:feishu:bot1", "feishu:bot1")
        msg = OutboundMessage(channel="proxy:feishu:bot1", chat_id="u1", content="Proxy msg")
        await deliver_fn(msg)
        app.proxy_manager.deliver_to_proxy.assert_awaited_once()
        assert not app.bus.publish_outbound.called

    @pytest.mark.asyncio
    async def test_proxy_failure_logged(self, app, deliver_fn):
        app.proxy_manager.has_proxy.return_value = True
        app.proxy_manager.deliver_to_proxy = AsyncMock(return_value=False)
        msg = OutboundMessage(channel="proxy:feishu:bot1", chat_id="u1", content="Fail")

        with patch("nanobot.gateway.app.logger.warning") as log_warn:
            await deliver_fn(msg)

        log_warn.assert_called_once()
        assert "Failed to deliver" in log_warn.call_args[0][0]


class TestConsumeOutbound:
    @pytest.mark.asyncio
    async def test_skips_streaming_messages(self):
        app = _make_mocked_app()
        stream_msg = MagicMock()
        stream_msg.metadata = {"_stream_delta": True}
        app.bus.consume_outbound = AsyncMock(side_effect=[
            stream_msg,
            asyncio.CancelledError(),
        ])

        with patch.object(app, "proxy_manager") as pm:
            try:
                await app._consume_outbound()
            except asyncio.CancelledError:
                pass

        pm.deliver_to_proxy.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_without_session_key(self):
        app = _make_mocked_app()
        msg_no_key = MagicMock()
        msg_no_key.metadata = {}
        app.bus.consume_outbound = AsyncMock(side_effect=[
            msg_no_key,
            asyncio.CancelledError(),
        ])

        with patch.object(app, "proxy_manager") as pm:
            try:
                await app._consume_outbound()
            except asyncio.CancelledError:
                pass

        pm.deliver_to_proxy.assert_not_called()

    @pytest.mark.asyncio
    async def test_proxy_delivery_with_session_key(self):
        app = _make_mocked_app()
        app.proxy_manager.has_proxy = MagicMock(return_value=True)
        app.proxy_manager.deliver_to_proxy = AsyncMock()

        valid_msg = MagicMock()
        valid_msg.metadata = {"_session_key": "feishu:bot1:u1"}
        valid_msg.channel = "proxy:feishu:bot1"
        valid_msg.chat_id = "u1"
        valid_msg.content = "Outbound"
        valid_msg.media = None
        valid_msg.buttons = None

        app.bus.consume_outbound = AsyncMock(side_effect=[
            valid_msg,
            asyncio.CancelledError(),
        ])

        try:
            await app._consume_outbound()
        except asyncio.CancelledError:
            pass

        app.proxy_manager.deliver_to_proxy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancellederror_breaks_loop(self):
        app = _make_mocked_app()
        app.bus.consume_outbound = AsyncMock(side_effect=asyncio.CancelledError())
        await app._consume_outbound()  # should exit cleanly
