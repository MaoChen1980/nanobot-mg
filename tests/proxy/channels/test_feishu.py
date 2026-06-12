"""Tests for FeishuProxyChannel — _download_media path traversal prevention."""

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
