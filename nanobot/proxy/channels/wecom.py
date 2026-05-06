"""WeCom proxy - runs as a separate process, connects to WeCom WebSocket and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import threading
from typing import Any

from loguru import logger

from nanobot.proxy.protocol import HubResponse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WeCom proxy - connects to WeCom WebSocket and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class WecomProxyChannel:
    """Handles WeCom message events and forwards to Hub via TCP."""

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._client: Any = None
        self._processed: dict[str, None] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()
        self._chat_frames: dict[str, Any] = {}

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


    def _process_message(self, frame: Any, msg_type: str) -> None:
        try:
            body = frame.body if hasattr(frame, "body") else (frame.get("body") if isinstance(frame, dict) else frame)
            if not isinstance(body, dict):
                return

            msg_id = body.get("msgid") or f"{body.get('chatid', '')}_{body.get('sendertime', '')}"
            if msg_id in self._processed:
                return
            self._processed[msg_id] = None
            if len(self._processed) > 1000:
                self._processed = dict(list(self._processed.items())[-500:])

            from_info = body.get("from", {})
            sender_id = from_info.get("userid", "unknown") if isinstance(from_info, dict) else "unknown"

            chat_id = body.get("chatid", sender_id)

            content_parts = []
            if msg_type == "text":
                text = body.get("text", {}).get("content", "")
                if text:
                    content_parts.append(text)
            elif msg_type == "voice":
                voice_content = body.get("voice", {}).get("content", "")
                if voice_content:
                    content_parts.append(f"[voice] {voice_content}")

            content = "\n".join(content_parts) if content_parts else ""
            if not content:
                return

            self._chat_frames[chat_id] = frame

            def forward():
                try:
                    with self._send_lock:
                        future = asyncio.run_coroutine_threadsafe(
                            self._send_with_reconnect({
                                "channel": self.channel,
                                "bot": self.bot,
                                "sender_id": sender_id,
                                "chat_id": chat_id,
                                "content": content,
                                "message_id": msg_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }),
                            self._conn_loop,
                        )
                        response = future.result(timeout=300)

                    if response and response.success and response.content:
                        self._send_reply(frame, response.content)
                except Exception as e:
                    logger.error("Failed to forward message after retries: {}, exiting process", e)
                    os._exit(1)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("WeCom proxy message handler error: {}", e)

    def _send_reply(self, frame: Any, content: str) -> None:
        if not self._client:
            return
        try:
            stream_id = self._generate_req_id("stream")
            self._client.reply_stream(frame, stream_id, content, finish=True)
        except Exception as e:
            logger.error("WeCom reply error: {}", e)

    def _generate_req_id(self, prefix: str) -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:8]}"


def run_wecom_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: WecomProxyChannel,
) -> None:
    from wecom_aibot_sdk import WSClient, generate_req_id

    proxy_channel._generate_req_id = generate_req_id

    client = WSClient({
        "bot_id": config.get("bot_id", ""),
        "secret": config.get("secret", ""),
        "reconnect_interval": 1000,
        "max_reconnect_attempts": -1,
        "heartbeat_interval": 30000,
    })
    proxy_channel._client = client

    client.on("connected", lambda f: logger.info("WeCom WebSocket connected"))
    client.on("authenticated", lambda f: logger.info("WeCom authenticated"))
    client.on("disconnected", lambda f: logger.warning("WeCom WebSocket disconnected"))
    client.on("message.text", lambda f: proxy_channel._process_message(f, "text"))
    client.on("message.image", lambda f: proxy_channel._process_message(f, "image"))
    client.on("message.voice", lambda f: proxy_channel._process_message(f, "voice"))
    client.on("message.file", lambda f: proxy_channel._process_message(f, "file"))
    client.on("message.mixed", lambda f: proxy_channel._process_message(f, "mixed"))

    def run_ws() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.connect_async())
        except Exception as e:
            logger.error("WeCom WS error: {}", e)
        finally:
            loop.close()

    thread = threading.Thread(target=run_ws, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("bot_id") or not config.get("secret"):
        logger.error("WeCom proxy: bot_id and secret required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("WeCom proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = WecomProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_wecom_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start WeCom proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WeCom proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
