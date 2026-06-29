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
    """_background_reader dispatches all delivers to _handle_deliver."""

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
        """All type='deliver' messages go to _handle_deliver."""
        deliver_line = json.dumps({
            "type": "deliver", "chat_id": "oc_xxx", "content": "reminder",
        }) + "\n"
        channel._reader.readline = AsyncMock(side_effect=[
            deliver_line.encode(),
            b"",  # EOF — breaks loop
        ])
        handle_deliver = AsyncMock()
        channel._handle_deliver = handle_deliver

        with patch("os._exit"):
            await channel._background_reader()

        handle_deliver.assert_awaited_once_with({
            "type": "deliver", "chat_id": "oc_xxx", "content": "reminder",
        })

    async def test_deliver_with_seq_still_goes_to_handle_deliver(self, channel):
        """Delivers with _seq (legacy hub) now also go to _handle_deliver."""
        deliver_line = json.dumps({
            "type": "deliver", "_seq": 42, "success": True, "content": "ok",
        }) + "\n"
        channel._reader.readline = AsyncMock(side_effect=[
            deliver_line.encode(),
            b"",  # EOF
        ])
        handle_deliver = AsyncMock()
        channel._handle_deliver = handle_deliver

        with patch("os._exit"):
            await channel._background_reader()

        handle_deliver.assert_awaited_once_with({
            "type": "deliver", "_seq": 42, "success": True, "content": "ok",
        })

    async def test_eof_exits_process(self, channel):
        """EOF on the reader (hub disconnected) triggers os._exit(1)."""
        channel._reader.readline = AsyncMock(side_effect=[b""])
        with patch("os._exit") as mock_exit:
            await channel._background_reader()
            mock_exit.assert_called_once_with(1)

    async def test_reader_error_exits_process(self, channel):
        """Reader exception triggers os._exit(1)."""
        channel._reader.readline = AsyncMock(side_effect=ConnectionResetError("connection lost"))
        with patch("os._exit") as mock_exit:
            await channel._background_reader()
            mock_exit.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# FeishuProxyChannel._handle_deliver
# ---------------------------------------------------------------------------


class TestFeishuHandleDeliver:
    """FeishuProxyChannel._handle_deliver sends messages via _send_formatted_reply."""

    @pytest.fixture
    def channel(self):
        ch = FeishuProxyChannel(
            config={"appId": "test", "appSecret": "test"},
            hub_tcp_host="127.0.0.1",
            hub_tcp_port=9999,
            channel="feishu",
            bot="nanobot",
        )
        ch._send_formatted_reply = MagicMock()
        return ch


    async def test_deliver_empty_chat_id(self, channel):
        await channel._handle_deliver({"chat_id": "", "content": "some content"})
        channel._send_formatted_reply.assert_not_called()

    async def test_deliver_empty_content(self, channel):
        await channel._handle_deliver({"chat_id": "oc_xxx", "content": ""})
        channel._send_formatted_reply.assert_not_called()

    async def test_deliver_missing_keys(self, channel):
        await channel._handle_deliver({})
        channel._send_formatted_reply.assert_not_called()


# ---------------------------------------------------------------------------
# FeishuProxyChannel._convert_tables_to_code_fences & _has_rich_content
# ---------------------------------------------------------------------------


class TestFeishuConvertTablesToCodeFences:
    """_convert_tables_to_code_fences (ported from openclaw's convertMarkdownTables)."""

    def test_no_table_passthrough(self):
        result = FeishuProxyChannel._convert_tables_to_code_fences("**bold** and `code`")
        assert result == "**bold** and `code`"

    def test_wraps_table_in_code_fence(self):
        content = "before\n| A | B |\n|---|---|\n| 1 | 2 |\nafter"
        result = FeishuProxyChannel._convert_tables_to_code_fences(content)
        assert "```" in result
        assert "| A | B |" in result
        parts = result.split("```")
        assert len(parts) == 3
        assert "| A | B |" in parts[1]
        assert "| 1 | 2 |" in parts[1]

    def test_no_wrap_for_single_pipe_line(self):
        result = FeishuProxyChannel._convert_tables_to_code_fences("| just a line")
        assert "```" not in result

    def test_multiple_tables(self):
        content = "| A | B |\n|---|---|\n| 1 | 2 |\n\n| X | Y |\n|---|---|\n| 3 | 4 |"
        result = FeishuProxyChannel._convert_tables_to_code_fences(content)
        assert result.count("```") == 4

    def test_table_at_end_of_content(self):
        content = "text\n| A | B |\n|---|---|\n| 1 | 2 |"
        result = FeishuProxyChannel._convert_tables_to_code_fences(content)
        assert "```" in result

    def test_pipe_in_normal_text_not_wrapped(self):
        content = "this | is not a table"
        result = FeishuProxyChannel._convert_tables_to_code_fences(content)
        assert "```" not in result

    def test_empty_content(self):
        assert FeishuProxyChannel._convert_tables_to_code_fences("") == ""
        assert FeishuProxyChannel._convert_tables_to_code_fences("\n\n") == "\n\n"

    def test_two_row_header_table(self):
        content = "| | QwenPaw | nanobot |\n|---|---|---|\n| 多 Looper | ✅ | ❌ |"
        result = FeishuProxyChannel._convert_tables_to_code_fences(content)
        assert "```" in result
        assert "| | QwenPaw | nanobot |" in result


class TestFeishuHasRichContent:
    """_has_rich_content detects tables and code blocks."""

    def test_plain_text(self):
        assert not FeishuProxyChannel._has_rich_content("hello world")

    def test_code_block(self):
        assert FeishuProxyChannel._has_rich_content("before\n```\ncode\n```\nafter")

    def test_markdown_table(self):
        assert FeishuProxyChannel._has_rich_content("| A | B |\n|---|---|\n| 1 | 2 |")

    def test_bold_text_only(self):
        assert not FeishuProxyChannel._has_rich_content("**bold** and `inline`")

    def test_empty(self):
        assert not FeishuProxyChannel._has_rich_content("")


class TestFeishuExtractHeader:
    """_extract_header extracts first # heading for card header bar."""

    def test_extracts_h1_at_start(self):
        header, body = FeishuProxyChannel._extract_header("# Hello\n\nSome text")
        assert header == "Hello"
        assert body == "Some text"

    def test_extracts_h1_with_leading_blanks(self):
        header, body = FeishuProxyChannel._extract_header("\n\n# Title\n\nBody text")
        assert header == "Title"
        assert "Body text" in body

    def test_no_header_returns_none(self):
        header, body = FeishuProxyChannel._extract_header("Just plain text\n\nNo heading")
        assert header is None
        assert body == "Just plain text\n\nNo heading"

    def test_skips_h2_h3(self):
        """Only H1 (#) triggers header bar — H2/3 stay in the body."""
        header, body = FeishuProxyChannel._extract_header("## Section\n\nContent")
        assert header is None
        assert "## Section" in body

    def test_header_removed_from_body(self):
        """The heading line is stripped from body so it doesn't render twice."""
        header, body = FeishuProxyChannel._extract_header(
            "# Header\n\nparagraph1\n\nparagraph2",
        )
        assert header == "Header"
        assert "Header" not in body

    def test_empty_content(self):
        header, body = FeishuProxyChannel._extract_header("")
        assert header is None
        assert body == ""
