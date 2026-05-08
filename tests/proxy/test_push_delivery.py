"""Tests for proxy push delivery mechanism — cron reminders and async messages to proxy channels."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.proxy.channels.base import BaseProxyChannel
from nanobot.proxy.channels.feishu import FeishuProxyChannel
from nanobot.proxy.manager import ProxyInfo, ProxyManager


# ---------------------------------------------------------------------------
# ProxyManager.deliver_to_proxy
# ---------------------------------------------------------------------------


class TestDeliverToProxy:
    """deliver_to_proxy writes JSON to the proxy's TCP writer."""

    async def test_deliver_success(self):
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.drain = AsyncMock()
        mgr = ProxyManager("http://hub:8080")
        info = ProxyInfo(
            channel="feishu", bot="nanobot",
            process=MagicMock(), registration={},
        )
        info.writer = writer
        mgr._proxies["feishu:nanobot"] = info

        result = await mgr.deliver_to_proxy(
            "feishu:nanobot",
            {"type": "deliver", "chat_id": "oc_xxx", "content": "hello"},
        )

        assert result is True
        writer.write.assert_called_once()
        written = writer.write.call_args[0][0].decode()
        assert json.loads(written) == {
            "type": "deliver", "chat_id": "oc_xxx", "content": "hello",
        }
        writer.drain.assert_awaited_once()

    async def test_deliver_no_such_proxy(self):
        mgr = ProxyManager("http://hub:8080")
        result = await mgr.deliver_to_proxy("nonexistent:bot", {"type": "deliver"})
        assert result is False

    async def test_deliver_no_writer(self):
        mgr = ProxyManager("http://hub:8080")
        info = ProxyInfo(
            channel="feishu", bot="nanobot",
            process=MagicMock(), registration={},
        )
        info.writer = None
        mgr._proxies["feishu:nanobot"] = info

        result = await mgr.deliver_to_proxy("feishu:nanobot", {"type": "deliver"})
        assert result is False

    async def test_deliver_writer_closing(self):
        writer = MagicMock()
        writer.is_closing.return_value = True
        mgr = ProxyManager("http://hub:8080")
        info = ProxyInfo(
            channel="feishu", bot="nanobot",
            process=MagicMock(), registration={},
        )
        info.writer = writer
        mgr._proxies["feishu:nanobot"] = info

        result = await mgr.deliver_to_proxy("feishu:nanobot", {"type": "deliver"})
        assert result is False

    async def test_deliver_write_error(self):
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.drain = AsyncMock()
        writer.write.side_effect = ConnectionError("broken pipe")
        mgr = ProxyManager("http://hub:8080")
        info = ProxyInfo(
            channel="feishu", bot="nanobot",
            process=MagicMock(), registration={},
        )
        info.writer = writer
        mgr._proxies["feishu:nanobot"] = info

        result = await mgr.deliver_to_proxy(
            "feishu:nanobot", {"type": "deliver"},
        )
        assert result is False


# ---------------------------------------------------------------------------
# BaseProxyChannel background reader + _handle_deliver
# ---------------------------------------------------------------------------


class TestBackgroundReader:
    """_background_reader dispatches 'deliver' type and fulfills pending responses."""

    @pytest.fixture
    def channel(self):
        ch = BaseProxyChannel(
            config={},
            hub_tcp_host="127.0.0.1",
            hub_tcp_port=9999,
            channel="test",
            bot="testbot",
        )
        ch._reader = AsyncMock()
        return ch

    async def test_deliver_dispatched_to_handle_deliver(self, channel):
        """Messages with type='deliver' go to _handle_deliver, not pending_response."""
        deliver_line = json.dumps({
            "type": "deliver", "chat_id": "oc_xxx", "content": "reminder",
        }) + "\n"
        channel._reader.readline = AsyncMock(side_effect=[
            deliver_line.encode(),
            b"",  # EOF — breaks loop
        ])
        handle_deliver = AsyncMock()
        channel._handle_deliver = handle_deliver

        await channel._background_reader()

        handle_deliver.assert_awaited_once_with({
            "type": "deliver", "chat_id": "oc_xxx", "content": "reminder",
        })

    async def test_non_deliver_fulfills_pending_response(self, channel):
        """Non-deliver messages fulfill the pending response future."""
        response_line = json.dumps({
            "success": True, "content": "ok",
        }) + "\n"
        channel._reader.readline = AsyncMock(side_effect=[
            response_line.encode(),
            b"",  # EOF
        ])
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        channel._pending_response = future

        await channel._background_reader()

        assert future.done()
        result = future.result()
        assert result["success"] is True
        assert result["content"] == "ok"

    async def test_deliver_does_not_fulfill_pending_response(self, channel):
        """Deliver messages should NOT fulfill the pending response."""
        deliver_line = json.dumps({
            "type": "deliver", "chat_id": "oc_xxx", "content": "reminder",
        }) + "\n"
        channel._reader.readline = AsyncMock(side_effect=[
            deliver_line.encode(),
            b"",  # EOF
        ])
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        channel._pending_response = future

        handle_deliver = AsyncMock()
        channel._handle_deliver = handle_deliver

        await channel._background_reader()

        # Pending response should NOT be set by a deliver message
        assert not future.done()
        handle_deliver.assert_awaited_once()

    async def test_reader_task_cancelled_on_reconnect(self, channel):
        """_reconnect_to_hub cancels old reader task."""
        loop = asyncio.get_running_loop()

        old_task = asyncio.create_task(asyncio.sleep(9999))
        channel._reader_task = old_task
        channel._writer = MagicMock()
        channel._writer.is_closing.return_value = True

        with patch.object(channel, "_enable_tcp_keepalive"):
            with patch.object(asyncio, "open_connection", side_effect=ConnectionError("refused")):
                result = await channel._reconnect_to_hub(max_retries=1)

        assert result is False
        assert old_task.cancelled()

    async def test_reconnect_starts_new_reader_on_success(self, channel):
        """After successful reconnection, a new background reader is started."""
        loop = asyncio.get_running_loop()

        old_task = asyncio.create_task(asyncio.sleep(9999))
        channel._reader_task = old_task
        channel._writer = MagicMock()
        channel._writer.is_closing.return_value = False
        channel._writer.close = MagicMock()
        channel._writer.wait_closed = AsyncMock()

        reader_mock = AsyncMock()
        writer_mock = MagicMock()
        writer_mock.drain = AsyncMock()

        async def mock_open_connection(host, port):
            return reader_mock, writer_mock

        with patch.object(channel, "_enable_tcp_keepalive"):
            with patch.object(channel, "_start_background_reader", AsyncMock()) as start_bg:
                with patch.object(asyncio, "open_connection", side_effect=mock_open_connection):
                    reader_mock.readline = AsyncMock(
                        side_effect=[json.dumps({"success": True}).encode()],
                    )
                    result = await channel._reconnect_to_hub(max_retries=1)

        assert result is True
        assert old_task.cancelled()
        start_bg.assert_awaited_once()


# ---------------------------------------------------------------------------
# FeishuProxyChannel._handle_deliver
# ---------------------------------------------------------------------------


class TestFeishuHandleDeliver:
    """FeishuProxyChannel._handle_deliver sends messages via _send_text_reply."""

    @pytest.fixture
    def channel(self):
        ch = FeishuProxyChannel(
            config={"appId": "test", "appSecret": "test"},
            hub_tcp_host="127.0.0.1",
            hub_tcp_port=9999,
            channel="feishu",
            bot="nanobot",
        )
        ch._send_text_reply = MagicMock()
        return ch

    async def test_deliver_sends_reply(self, channel):
        await channel._handle_deliver({
            "chat_id": "oc_xxx",
            "content": "cron reminder text",
        })
        channel._send_text_reply.assert_called_once_with(
            "oc_xxx", None, "cron reminder text",
        )

    async def test_deliver_empty_chat_id(self, channel):
        await channel._handle_deliver({"chat_id": "", "content": "some content"})
        channel._send_text_reply.assert_not_called()

    async def test_deliver_empty_content(self, channel):
        await channel._handle_deliver({"chat_id": "oc_xxx", "content": ""})
        channel._send_text_reply.assert_not_called()

    async def test_deliver_missing_keys(self, channel):
        await channel._handle_deliver({})
        channel._send_text_reply.assert_not_called()
