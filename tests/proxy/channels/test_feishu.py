"""Tests for FeishuProxyChannel — _download_media path traversal prevention and _send_media error notification."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

from nanobot.proxy.channels.feishu import FeishuProxyChannel


class MockResponse:
    """Simulate Feishu SDK message_resource.get response."""
    def __init__(self, file_name: str = "", success: bool = True):
        self._file_name = file_name
        self._success = success
        self.file = io.BytesIO(b"fake_image_data")
        self.code = 0
        self.msg = "success"

    def success(self) -> bool:
        return self._success

    @property
    def file_name(self) -> str:
        return self._file_name


def test_path_traversal_prevented(tmp_path):
    """Malicious file_name writes inside incoming/ only."""
    channel = FeishuProxyChannel(
        config={"_workspace_path": str(tmp_path), "appId": "test", "appSecret": "test"},
        hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
        channel="feishu", bot="test",
    )
    channel._client = MagicMock()
    channel._client.im.v1.message_resource.get.return_value = MockResponse(
        file_name="../../etc/passwd"
    )
    channel._guess_ext_from_resp = MagicMock(return_value=".png")

    result = channel._download_media("fake_key", "image", {})
    assert result is not None

    resolved = Path(result).resolve()
    expected_dir = (tmp_path / "incoming").resolve()
    assert expected_dir in resolved.parents or resolved.parent == expected_dir
    assert resolved.name == "passwd"


def test_normal_filename_works(tmp_path):
    """Normal filename writes to incoming/ correctly."""
    channel = FeishuProxyChannel(
        config={"_workspace_path": str(tmp_path), "appId": "test", "appSecret": "test"},
        hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
        channel="feishu", bot="test",
    )
    channel._client = MagicMock()
    channel._client.im.v1.message_resource.get.return_value = MockResponse(
        file_name="image_001.png"
    )
    channel._guess_ext_from_resp = MagicMock(return_value=".png")

    result = channel._download_media("fake_key", "image", {})
    assert result is not None
    assert Path(result).name == "image_001.png"


def test_empty_filename_uses_fallback(tmp_path):
    """When file_name is empty, a timestamp-based fallback is used."""
    channel = FeishuProxyChannel(
        config={"_workspace_path": str(tmp_path), "appId": "test", "appSecret": "test"},
        hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
        channel="feishu", bot="test",
    )
    channel._client = MagicMock()
    resp = MockResponse(file_name="")
    channel._client.im.v1.message_resource.get.return_value = resp
    channel._guess_ext_from_resp = MagicMock(return_value=".png")

    result = channel._download_media("abc123", "image", {})
    assert result is not None
    assert "abc123" in result
    assert result.endswith(".png")


class MockFeishuChannel(FeishuProxyChannel):
    """Subclass with mocked client for _send_media tests."""
    def __init__(self, tmp_path):
        super().__init__(
            config={"_workspace_path": str(tmp_path), "appId": "test", "appSecret": "test"},
            hub_tcp_host="127.0.0.1", hub_tcp_port=9999,
            channel="feishu", bot="test",
        )
        self._client = MagicMock()
        self.sent_errors: list[str] = []

    def _send_plain_text(self, chat_id: str, content: str, root_id: str | None = None) -> None:
        self.sent_errors.append(content)


def test_send_media_file_not_found_reports_error(tmp_path):
    """File not found sends error notification instead of silent skip."""
    ch = MockFeishuChannel(tmp_path)
    ch._send_media("chat_1", None, ["/nonexistent/file.pptx"], msg_type="file")
    assert len(ch.sent_errors) == 1
    assert "找不到文件" in ch.sent_errors[0]


def test_send_media_upload_failure_reports_error(tmp_path):
    """Upload failure sends error notification."""
    ch = MockFeishuChannel(tmp_path)
    f = tmp_path / "test.pptx"
    f.write_bytes(b"fake pptx content")
    ch._upload_media_to_feishu = MagicMock(return_value=None)  # upload fails
    ch._send_media("chat_1", None, [str(f)], msg_type="file")
    assert len(ch.sent_errors) == 1
    assert "出错" in ch.sent_errors[0]


def test_send_media_api_error_reports_error(tmp_path):
    """API error after upload sends error notification."""
    ch = MockFeishuChannel(tmp_path)
    f = tmp_path / "test.pptx"
    f.write_bytes(b"fake pptx content")
    ch._upload_media_to_feishu = MagicMock(return_value="valid_file_key")

    mock_resp = MagicMock()
    mock_resp.code = 234006
    mock_resp.msg = "file too large"
    ch._client.im.v1.message.create.return_value = mock_resp

    ch._send_media("chat_1", None, [str(f)], msg_type="file")
    assert len(ch.sent_errors) == 1
    assert "file too large" in ch.sent_errors[0]


def test_send_media_exception_reports_error(tmp_path):
    """Exception during send sends error notification."""
    ch = MockFeishuChannel(tmp_path)
    f = tmp_path / "test.pptx"
    f.write_bytes(b"fake pptx content")
    ch._upload_media_to_feishu = MagicMock(return_value="valid_file_key")
    ch._client.im.v1.message.create.side_effect = RuntimeError("connection lost")

    ch._send_media("chat_1", None, [str(f)], msg_type="file")
    assert len(ch.sent_errors) == 1
    assert "connection lost" in ch.sent_errors[0]


def test_send_media_success_no_error(tmp_path):
    """Successful send does NOT send error notification."""
    ch = MockFeishuChannel(tmp_path)
    f = tmp_path / "test.pptx"
    f.write_bytes(b"fake pptx content")
    ch._upload_media_to_feishu = MagicMock(return_value="valid_file_key")

    mock_resp = MagicMock()
    mock_resp.code = 0
    mock_resp.msg = "success"
    ch._client.im.v1.message.create.return_value = mock_resp

    ch._send_media("chat_1", None, [str(f)], msg_type="file")
    assert len(ch.sent_errors) == 0
