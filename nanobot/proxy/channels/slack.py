"""Slack proxy - runs as a separate process, connects to Slack via Socket Mode and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import threading
from typing import Any

from loguru import logger

from nanobot.proxy.protocol import HubResponse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slack proxy - connects to Slack Socket Mode and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class SlackProxyChannel:
    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._web_client: Any = None
        self._socket_client: Any = None
        self._bot_user_id: str | None = None
        self._processed: dict[str, float] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()

    def _connect_tcp(self) -> None:
        self._conn_loop = asyncio.new_event_loop()
        self._conn_thread = threading.Thread(target=self._conn_loop.run_forever, daemon=True)
        self._conn_thread.start()

        async def do_connect() -> None:
            self._reader, self._writer = await asyncio.open_connection(
                self.hub_tcp_host, self.hub_tcp_port
            )
            logger.info("Connected to Hub via TCP at {}:{}", self.hub_tcp_host, self.hub_tcp_port)
            register_msg = {"type": "register", "channel": self.channel, "bot": self.bot, "pid": os.getpid()}
            self._writer.write((json.dumps(register_msg) + "\n").encode())
            await self._writer.drain()
            resp_line = await self._reader.readline()
            resp = json.loads(resp_line.decode())
            if resp.get("success"):
                logger.info("Registered with Hub via TCP")
            else:
                raise RuntimeError(f"TCP registration failed: {resp}")

        future = asyncio.run_coroutine_threadsafe(do_connect(), self._conn_loop)
        future.result()

    async def _do_send(self, msg: dict[str, Any]) -> HubResponse:
        msg["type"] = "message"
        self._writer.write((json.dumps(msg) + "\n").encode())
        await self._writer.drain()
        resp_line = await self._reader.readline()
        return HubResponse.from_dict(json.loads(resp_line.decode()))

    async def _reconnect_to_hub(self, max_retries: int = 3) -> bool:
        """Reconnect to Hub via TCP with exponential backoff. Runs on conn_loop."""
        for attempt in range(1, max_retries + 1):
            try:
                if self._writer and not self._writer.is_closing():
                    self._writer.close()
                    try:
                        await self._writer.wait_closed()
                    except Exception:
                        pass

                self._reader, self._writer = await asyncio.open_connection(
                    self.hub_tcp_host, self.hub_tcp_port
                )
                logger.info("Reconnected to Hub via TCP (attempt {})", attempt)

                register_msg = {
                    "type": "register",
                    "channel": self.channel,
                    "bot": self.bot,
                    "pid": os.getpid(),
                }
                self._writer.write((json.dumps(register_msg) + "\n").encode())
                await self._writer.drain()

                resp_line = await self._reader.readline()
                resp = json.loads(resp_line.decode())
                if resp.get("success"):
                    logger.info("Re-registered with Hub via TCP (attempt {})", attempt)
                    return True
            except Exception as e:
                logger.warning("Reconnect attempt {}/{} failed: {}", attempt, max_retries, e)

            if attempt < max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        return False

    async def _send_with_reconnect(self, msg: dict[str, Any]) -> HubResponse:
        """Send message to Hub via TCP, with automatic reconnect on failure."""
        last_error = None
        for attempt in range(3):
            try:
                return await self._do_send(msg)
            except Exception as e:
                last_error = e
                logger.warning("Send attempt {}/3 failed: {}", attempt + 1, e)
                if attempt < 2:
                    if not await self._reconnect_to_hub():
                        break
        raise RuntimeError(f"Send failed after 3 attempts: {last_error}")


    async def _on_socket_request(self, client: Any, req: Any) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        if req.type == "interactive":
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            payload = req.payload or {}
            actions = payload.get("actions") or []
            if not actions:
                return
            value = str(actions[0].get("value") or "")
            user_info = payload.get("user") or {}
            sender_id = str(user_info.get("id") or "")
            channel_info = payload.get("channel") or {}
            chat_id = str(channel_info.get("id") or "")
            if not sender_id or not chat_id or not value:
                return
            await self._handle_text_message(sender_id, chat_id, value, req)
            return

        if req.type != "events_api":
            return

        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = event.get("type")

        if event_type not in ("message", "app_mention"):
            return

        sender_id = event.get("user")
        chat_id = event.get("channel")
        if not sender_id or not chat_id:
            return

        if self._bot_user_id and sender_id == self._bot_user_id:
            return

        subtype = event.get("subtype")
        if subtype and subtype != "file_share":
            return

        text = event.get("text") or ""
        if not text:
            return

        await self._handle_text_message(sender_id, chat_id, text, req)

    async def _handle_text_message(self, sender_id: str, chat_id: str, text: str, req: Any) -> None:
        now = time.time()
        key = f"{chat_id}:{text[:50]}"
        if key in self._processed and now - self._processed[key] < 2:
            return
        self._processed[key] = now

        try:
            response = await self._send_with_reconnect({
                "channel": self.channel,
                "bot": self.bot,
                "sender_id": sender_id,
                "chat_id": chat_id,
                "content": text,
                "message_id": req.envelope_id or f"{chat_id}:{now}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

            if response and response.success and response.content:
                await self._web_client.chat_postMessage(
                    channel=chat_id,
                    text=response.content,
                )
        except Exception as e:
            logger.error("Failed to forward message after retries: {}, exiting process", e)
            os._exit(1)


def run_slack_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: SlackProxyChannel,
) -> None:
    from slack_sdk.socket_mode.websockets import SocketModeClient
    from slack_sdk.web.async_client import AsyncWebClient

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    web_client = AsyncWebClient(token=config.get("bot_token", ""))
    socket_client = SocketModeClient(
        app_token=config.get("app_token", ""),
        web_client=web_client,
    )
    proxy_channel._web_client = web_client
    proxy_channel._socket_client = socket_client

    socket_client.socket_mode_request_listeners.append(lambda c, r: proxy_channel._on_socket_request(c, r))

    def run():
        loop.run_until_complete(socket_client.connect())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("bot_token") or not config.get("app_token"):
        logger.error("Slack proxy: bot_token and app_token required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("Slack proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = SlackProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_slack_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start Slack proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Slack proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
