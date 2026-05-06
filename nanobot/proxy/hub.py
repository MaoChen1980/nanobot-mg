"""Proxy TCP server for hub <-> proxy communication.

Manages TCP connections with proxy processes. Each proxy maintains
a long-lived TCP connection for messages and responses. No HTTP,
no heartbeat — connection liveness is the heartbeat.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from nanobot.proxy.manager import ProxyManager
from nanobot.proxy.protocol import HubResponse, ProxyMessage


class HubTCPServer:
    """TCP server that accepts proxy connections and routes messages to AgentLoop.

    Protocol:
    - Proxy sends JSON lines (ProxyMessage) over TCP
    - Hub responds with JSON lines (HubResponse) over same TCP
    - Connection close = proxy death signal
    """

    def __init__(
        self,
        host: str,
        port: int,
        agent_loop: Any,
        proxy_manager: ProxyManager,
        concurrency_gate: asyncio.Semaphore | None = None,
    ):
        self._host = host
        self._port = port
        self._agent_loop = agent_loop
        self._proxy_manager = proxy_manager
        self._concurrency_gate = concurrency_gate
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        """Start the TCP server and begin accepting proxy connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
        )
        logger.info("Proxy TCP server listening on {}:{}", self._host, self._port)

    async def stop(self) -> None:
        """Stop the TCP server gracefully."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Proxy TCP server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single proxy TCP connection."""
        peername = writer.get_extra_info("peername")
        logger.info("Proxy TCP connection from {}", peername)

        try:
            while True:
                line_bytes = await reader.readline()
                if not line_bytes:
                    break  # EOF — connection closed

                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from proxy {}: {}", peername, line[:100])
                    continue

                msg_type = data.get("type", "")

                if msg_type == "ping":
                    writer.write((json.dumps({"type": "pong"}) + "\n").encode())
                    await writer.drain()

                elif msg_type == "register":
                    channel = data.get("channel", "")
                    bot = data.get("bot", "")
                    pid = data.get("pid", 0)
                    key = f"{channel}:{bot}"

                    accepted = self._proxy_manager.register_via_tcp(
                        key, reader, writer,
                        {"channel": channel, "bot": bot, "pid": pid},
                    )
                    if not accepted:
                        logger.warning(
                            "Proxy registration rejected: {}:{} (pid={})",
                            channel, bot, pid,
                        )
                        break

                    logger.info("Proxy registered: {}:{} (pid={})", channel, bot, pid)
                    resp = HubResponse(success=True)
                    writer.write((json.dumps(resp.to_dict()) + "\n").encode())
                    await writer.drain()

                elif msg_type == "message":
                    await self._route_message(writer, data, peername)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Proxy TCP connection error from {}: {}", peername, e)
        finally:
            logger.info("Proxy TCP disconnected: {}", peername)
            self._proxy_manager.unregister_by_writer(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _route_message(
        self,
        writer: asyncio.StreamWriter,
        data: dict[str, Any],
        peername: Any,
    ) -> None:
        """Deserialize a ProxyMessage, process it through the agent, and reply."""
        try:
            msg = ProxyMessage.from_dict(data)
        except Exception as e:
            logger.warning("Invalid ProxyMessage from proxy {}: {}", peername, e)
            resp = HubResponse(success=False, error=str(e))
            writer.write((json.dumps(resp.to_dict()) + "\n").encode())
            await writer.drain()
            return

        session_key = f"{msg.channel}:{msg.bot}:{msg.sender_id}"
        logger.info(
            "TCP proxy message for {}: {} (session={})",
            session_key, msg.content[:50], session_key,
        )

        inbound = msg.to_inbound_message()

        async def _process() -> Any:
            if self._concurrency_gate:
                async with self._concurrency_gate:
                    return await self._agent_loop.process_direct(
                        content=inbound.content,
                        session_key=session_key,
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        media=inbound.media or None,
                    )
            else:
                return await self._agent_loop.process_direct(
                    content=inbound.content,
                    session_key=session_key,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    media=inbound.media or None,
                )

        try:
            response = await _process()
            if response is None:
                resp = HubResponse(success=True, content="")
            else:
                resp = response.to_hub_response(reply_to=msg.message_id)
        except Exception as e:
            logger.exception("Error processing proxy TCP message: {}", e)
            resp = HubResponse(success=False, error=str(e))

        writer.write((json.dumps(resp.to_dict()) + "\n").encode())
        await writer.drain()
