"""Tests for EmailProxyChannel — _decode_header_value."""
from __future__ import annotations

from unittest.mock import patch

from nanobot.proxy.channels.email import EmailProxyChannel


class TestDecodeHeaderValue:
    """``EmailProxyChannel._decode_header_value`` — safe email header decoding."""

    def test_empty_string_returns_empty(self):
        assert EmailProxyChannel._decode_header_value("") == ""

    def test_plain_string_passes_through(self):
        assert EmailProxyChannel._decode_header_value("hello") == "hello"

    def test_encoded_word_decoded(self):
        result = EmailProxyChannel._decode_header_value("=?UTF-8?Q?hello?=")
        assert result == "hello"

    def test_corrupt_encoded_word_returns_original(self):
        """Malformed RFC 2047 encoded word -> exception path -> returns original."""
        result = EmailProxyChannel._decode_header_value("=?UTF-8?Q?=FF=FE?=")
        assert result == "=?UTF-8?Q?=FF=FE?="

    def test_mocked_exception_returns_original(self):
        """When decode_header raises, the original value is returned and warning logged."""
        with patch("nanobot.proxy.channels.email.decode_header", side_effect=ValueError("bad header")):
            result = EmailProxyChannel._decode_header_value("test")
        assert result == "test"

    def test_non_ascii_returns_original(self):
        """High-codepoint non-ASCII input should pass through as-is."""
        result = EmailProxyChannel._decode_header_value("中文")
        assert result == "中文"
