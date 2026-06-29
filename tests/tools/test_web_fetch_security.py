"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import pytest

from nanobot.agent.tools.web import WebFetchTool


def _fake_resolve_private(hostname, port=None, family=0, type=0, proto=0, flags=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port=None, family=0, type=0, proto=0, flags=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(url="http://169.254.169.254/computeMetadata/v1/")
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()
    def _resolve_localhost(hostname, port=None, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    with patch("nanobot.security.network.socket.getaddrinfo", _resolve_localhost):
        result = await tool.execute(url="http://localhost/admin")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_redirect_before_returning_image(monkeypatch):
    tool = WebFetchTool()

    class FakeStreamResponse:
        headers = {"content-type": "image/png"}
        url = "http://127.0.0.1/secret.png"
        content = b"\x89PNG\r\n\x1a\n"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return self.content

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, **kwargs):
            return FakeStreamResponse()

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/image.png")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_rejects_backtick_wrapped_url():
    """URL wrapped in backticks must fail validation (no silent stripping)."""
    tool = WebFetchTool()
    result = await tool.execute(url="`https://example.com`")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_rejects_single_quote_wrapped_url():
    """URL wrapped in single quotes must fail validation (no silent stripping)."""
    tool = WebFetchTool()
    result = await tool.execute(url="'https://example.com'")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_rejects_double_quote_wrapped_url():
    """URL wrapped in double quotes must fail validation (no silent stripping)."""
    tool = WebFetchTool()
    result = await tool.execute(url='"https://example.com"')
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_accepts_clean_url():
    """Clean URL without wrapping must still pass initial validation."""
    tool = WebFetchTool()
    def _resolve_public(hostname, port=None, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
    with patch("nanobot.security.network.socket.getaddrinfo", _resolve_public):
        result = await tool.execute(url="https://example.com")
    data = json.loads(result)
    # It may fail later (DNS/HTTP), but the initial validation should pass
    assert "Only http/https" not in data.get("error", "")
    assert "Missing domain" not in data.get("error", "")
