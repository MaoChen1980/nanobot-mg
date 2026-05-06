"""Proxy TCP server for hub <-> proxy communication.

Manages TCP connections with proxy processes. Each proxy maintains
a long-lived TCP connection for messages and responses. No HTTP,
no heartbeat — connection liveness is the heartbeat.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.proxy.manager import ProxyManager
from nanobot.proxy.protocol import HubResponse, ProxyMessage


async def _handle_proxy_tcp(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    agent_loop: AgentLoop,
    proxy_manager: ProxyManager,
) -> None:
    """Handle a single proxy TCP connection.

    Protocol:
    - Proxy sends JSON lines (ProxyMessage) over TCP
    - Hub responds with JSON lines (HubResponse) over same TCP
    - Connection close = proxy death signal
    """
    peername = writer.get_extra_info("peername")
    logger.info("Proxy TCP connection from {}", peername)

    try:
        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                # EOF — connection closed
                break

            line = line_bytes.decode("utf-8").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from proxy {}: {}", peername, line[:100])
                continue

            msg_type = data.get("type", "")

            if msg_type == "register":
                # Proxy registration
                channel = data.get("channel", "")
                bot = data.get("bot", "")
                pid = data.get("pid", 0)
                key = f"{channel}:{bot}"

                accepted = proxy_manager.register_via_tcp(
                    key, reader, writer, {"channel": channel, "bot": bot, "pid": pid},
                )
                if not accepted:
                    logger.warning("Proxy registration rejected: {}:{} (pid={})", channel, bot, pid)
                    break  # Connection will be closed in finally block

                logger.info("Proxy registered: {}:{} (pid={})", channel, bot, pid)
                resp = HubResponse(success=True)
                writer.write((json.dumps(resp.to_dict()) + "\n").encode())
                await writer.drain()

            elif msg_type == "message":
                # Forward message to agent
                try:
                    msg = ProxyMessage.from_dict(data)
                except Exception as e:
                    logger.warning("Invalid ProxyMessage from proxy {}: {}", peername, e)
                    resp = HubResponse(success=False, error=str(e))
                    writer.write((json.dumps(resp.to_dict()) + "\n").encode())
                    await writer.drain()
                    continue

                session_key = f"{msg.channel}:{msg.bot}:{msg.sender_id}"
                logger.info("TCP proxy message for {}: {} (session={})", session_key, msg.content[:50], session_key)

                try:
                    response = await agent_loop.process_direct(
                        content=msg.content,
                        session_key=session_key,
                        channel=f"proxy:{msg.channel}:{msg.bot}",
                        chat_id=msg.chat_id,
                        media=msg.media if msg.media else None,
                    )
                    if response is None:
                        resp = HubResponse(success=True, content="")
                    else:
                        reply_content = response.content if hasattr(response, "content") else str(response)
                        resp = HubResponse(
                            success=True,
                            reply_to=msg.message_id,
                            content=reply_content,
                            metadata=response.metadata if hasattr(response, "metadata") else {},
                        )
                except Exception as e:
                    logger.exception("Error processing proxy TCP message: {}", e)
                    resp = HubResponse(success=False, error=str(e))

                writer.write((json.dumps(resp.to_dict()) + "\n").encode())
                await writer.drain()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug("Proxy TCP connection error from {}: {}", peername, e)
    finally:
        logger.info("Proxy TCP disconnected: {}", peername)
        proxy_manager.unregister_by_writer(writer)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_tcp_server(
    host: str,
    port: int,
    agent_loop: AgentLoop,
    proxy_manager: ProxyManager,
) -> asyncio.Server:
    """Start TCP server for proxy connections."""
    server = await asyncio.start_server(
        partial(_handle_proxy_tcp, agent_loop=agent_loop, proxy_manager=proxy_manager),
        host=host,
        port=port,
    )
    logger.info("Proxy TCP server listening on {}:{}", host, port)
    return server


async def stop_tcp_server(server: asyncio.Server) -> None:
    """Stop the TCP server gracefully."""
    server.close()
    await server.wait_closed()
    logger.info("Proxy TCP server stopped")
