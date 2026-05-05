"""Proxy API endpoints for hub <-> proxy communication.

Adds /api/proxy/message, /api/proxy/register, /api/proxy/heartbeat
endpoints to an existing aiohttp application.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web
from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.proxy.manager import ProxyManager
from nanobot.proxy.protocol import HubResponse, ProxyMessage


async def _handle_message(request: web.Request) -> web.Response:
    """
    POST /api/proxy/message
    Proxy sends incoming message to hub for processing.
    """
    agent_loop: AgentLoop = request.app["agent_loop"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    try:
        msg = ProxyMessage.from_dict(body)
    except Exception:
        return web.json_response({"error": "invalid message format"}, status=400)

    session_key = f"{msg.channel}:{msg.bot}:{msg.sender_id}"

    logger.info(
        "Proxy message for {}: {} (session={})",
        session_key, msg.content[:50], session_key
    )

    try:
        response = await agent_loop.process_direct(
            content=msg.content,
            session_key=session_key,
            channel=f"proxy:{msg.channel}:{msg.bot}",
            chat_id=msg.chat_id,
            media=msg.media if msg.media else None,
        )

        if response is None:
            return web.json_response(
                HubResponse(success=True, content="").to_dict()
            )

        reply_content = response.content if hasattr(response, "content") else str(response)

        return web.json_response(
            HubResponse(
                success=True,
                reply_to=msg.message_id,
                content=reply_content,
                metadata=response.metadata if hasattr(response, "metadata") else {},
            ).to_dict()
        )
    except Exception as e:
        logger.exception("Error processing proxy message: {}", e)
        return web.json_response(
            HubResponse(success=False, error=str(e)).to_dict(),
            status=500
        )


async def _handle_register(request: web.Request) -> web.Response:
    """
    POST /api/proxy/register
    Proxy starts up and registers with hub.
    """
    manager: ProxyManager = request.app["proxy_manager"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    channel = body.get("channel", "")
    bot = body.get("bot", "")
    if not channel or not bot:
        return web.json_response({"error": "channel and bot required"}, status=400)

    manager.register(body)
    logger.info("Proxy registered: {}:{}", channel, bot)
    return web.json_response({"success": True})


async def _handle_heartbeat(request: web.Request) -> web.Response:
    """
    POST /api/proxy/heartbeat
    Proxy sends periodic heartbeat to keep alive.
    """
    manager: ProxyManager = request.app["proxy_manager"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    manager.heartbeat(body)
    logger.debug("Heartbeat received for {}:{}", body.get("channel"), body.get("bot"))
    return web.json_response({"success": True})


def setup_proxy_routes(
    app: web.Application,
    agent_loop: AgentLoop,
    proxy_manager: ProxyManager,
) -> None:
    """Add proxy API routes to an existing aiohttp app."""
    app["agent_loop"] = agent_loop
    app["proxy_manager"] = proxy_manager

    app.router.add_post("/api/proxy/message", _handle_message)
    app.router.add_post("/api/proxy/register", _handle_register)
    app.router.add_post("/api/proxy/heartbeat", _handle_heartbeat)

    logger.info("Proxy API routes registered")