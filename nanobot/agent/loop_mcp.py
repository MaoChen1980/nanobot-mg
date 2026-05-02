"""MCP connection management for AgentLoop."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


async def connect_mcp(loop: AgentLoop) -> None:
    """Connect to configured MCP servers (one-time, lazy)."""
    if loop._mcp_connected or loop._mcp_connecting or not loop._mcp_servers:
        return
    loop._mcp_connecting = True
    from nanobot.agent.tools.mcp import connect_mcp_servers

    try:
        loop._mcp_stacks = await connect_mcp_servers(loop._mcp_servers, loop.tools)
        if loop._mcp_stacks:
            loop._mcp_connected = True
            logger.info("MCP servers connected: {}", list(loop._mcp_stacks.keys()))
        else:
            logger.warning("No MCP servers connected successfully (will retry next message)")
    except asyncio.CancelledError:
        logger.warning("MCP connection cancelled (will retry next message)")
        loop._mcp_stacks.clear()
    except BaseException as e:
        logger.error("Failed to connect MCP servers (will retry next message): {}", e)
        loop._mcp_stacks.clear()
    finally:
        loop._mcp_connecting = False


async def close_mcp(loop: AgentLoop) -> None:
    """Drain pending background archives, then close MCP connections."""
    if loop._background_tasks:
        await asyncio.gather(*loop._background_tasks, return_exceptions=True)
        loop._background_tasks.clear()
    for name, stack in loop._mcp_stacks.items():
        try:
            await stack.aclose()
        except (RuntimeError, BaseExceptionGroup):
            logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
    loop._mcp_stacks.clear()