"""Tests for nanobot.api.server — HTTP handlers."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.api.server import handle_shutdown


def _json_body(resp):
    """Parse aiohttp Response body as JSON."""
    body = resp.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


@pytest.mark.asyncio
async def test_handle_shutdown_stops_proxy_and_restarts() -> None:
    """handle_shutdown stops proxies and spawns a delayed Python subprocess."""
    mock_proxy = AsyncMock()
    request = MagicMock()
    request.app.state.proxy_manager = mock_proxy

    with patch("time.sleep"):  # Make thread execute instantly
        with patch("subprocess.Popen") as mock_popen:
            with patch("os._exit"):
                resp = await handle_shutdown(request)

    # Response
    assert resp.status_code == 200
    data = _json_body(resp)
    assert data["ok"] is True
    assert "restart" in data["message"].lower()

    # Proxy manager was stopped
    mock_proxy.stop.assert_awaited_once()

    # Subprocess spawned with correct command
    mock_popen.assert_called_once()
    popen_args = mock_popen.call_args[0][0]
    assert popen_args[0] == sys.executable
    assert popen_args[1] == "-c"
    delay_code = popen_args[2]
    # Verify the child code structure
    assert "time.sleep(3)" in delay_code
    assert "-m" in delay_code
    assert "nanobot" in delay_code
    assert "gateway" in delay_code


@pytest.mark.asyncio
async def test_handle_shutdown_safe_without_proxy_manager() -> None:
    """handle_shutdown does not crash when no proxy_manager is configured."""
    request = MagicMock()
    request.app.state.proxy_manager = None

    with patch("time.sleep"):
        with patch("subprocess.Popen"):
            with patch("os._exit"):
                resp = await handle_shutdown(request)

    assert resp.status_code == 200
    data = _json_body(resp)
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_handle_config_update_invalid_json() -> None:
    """Invalid JSON body -> returns 400 error."""
    from nanobot.api.server import handle_config_update

    request = MagicMock()
    request.json = AsyncMock(side_effect=ValueError("bad json"))

    resp = await handle_config_update(request)
    assert resp.status_code == 400
    data = _json_body(resp)
    assert data["error"] == "Invalid JSON"


@pytest.mark.asyncio
async def test_handle_settings_update_invalid_json() -> None:
    """Invalid JSON body -> returns 400 error."""
    from nanobot.api.server import handle_settings_update

    request = MagicMock()
    request.json = AsyncMock(side_effect=ValueError("bad json"))

    resp = await handle_settings_update(request)
    assert resp.status_code == 400
    data = _json_body(resp)
    assert data["error"] == "Invalid JSON"


@pytest.mark.asyncio
async def test_handle_memory_chat_invalid_json() -> None:
    """Invalid JSON body -> returns 400 error."""
    from nanobot.api.server import handle_memory_chat

    request = MagicMock()
    request.json = AsyncMock(side_effect=ValueError("bad json"))

    resp = await handle_memory_chat(request)
    assert resp.status_code == 400
    data = _json_body(resp)
    assert data["error"] == "Invalid JSON"
