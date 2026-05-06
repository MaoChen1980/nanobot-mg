"""Feishu proxy - runs as a separate process, connects to Feishu WebSocket and forwards messages to nanobot Hub via TCP."""

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

from nanobot.proxy.protocol import HubResponse, ProxyMessage


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feishu proxy - connects to Feishu and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    """Get channel config from environment variable (set by ProxyManager)."""
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class FeishuProxyChannel:
    """Handles Feishu message events and forwards to Hub via TCP."""

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str, client: Any):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._client = client
        self._processed: set[str] = set()
        self._reaction_emoji = config.get("react_emoji", "THUMBSUP")
        self._done_emoji = config.get("done_emoji")
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()

    async def _do_connect(self) -> None:
        """Actual TCP connection logic (runs in thread)."""
        self._reader, self._writer = await asyncio.open_connection(
            self.hub_tcp_host, self.hub_tcp_port
        )
        logger.info("Connected to Hub via TCP at {}:{}", self.hub_tcp_host, self.hub_tcp_port)

        # Send registration
        register_msg = {
            "type": "register",
            "channel": self.channel,
            "bot": self.bot,
            "pid": os.getpid(),
        }
        self._writer.write((json.dumps(register_msg) + "\n").encode())
        await self._writer.drain()

        # Wait for registration response
        resp_line = await self._reader.readline()
        resp = json.loads(resp_line.decode())
        if resp.get("success"):
            logger.info("Registered with Hub via TCP")
        else:
            raise RuntimeError(f"TCP registration failed: {resp}")

    def _connect_tcp(self) -> None:
        """Connect to Hub via TCP in a dedicated thread with persistent loop."""
        self._conn_loop = asyncio.new_event_loop()
        self._conn_thread = threading.Thread(target=self._conn_loop.run_forever, daemon=True)
        self._conn_thread.start()

        # Run connection coroutine on _conn_loop (not ws_loop)
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
        future.result()  # block until connected

    async def _do_send(self, msg: dict[str, Any]) -> HubResponse:
        """Send message to Hub via TCP and wait for response (runs in conn_loop)."""
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

    def on_message(self, data: Any) -> None:
        """Sync callback from Feishu SDK - forward message to Hub."""
        logger.info("Feishu WS on_message called: data={}", type(data).__name__)
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = getattr(message, "message_id", None)
            if not message_id or message_id in self._processed:
                return
            self._processed.add(message_id)
            if len(self._processed) > 1000:
                self._processed = set(list(self._processed)[-500:])

            content = getattr(message, "content", "")
            try:
                content_obj = json.loads(content)
                text = content_obj.get("text", "") if isinstance(content_obj, dict) else str(content_obj)
            except Exception:
                text = content

            sender_id_obj = getattr(sender, "sender_id", None)
            if sender_id_obj is not None and hasattr(sender_id_obj, "open_id"):
                sender_id = sender_id_obj.open_id
            else:
                sender_id = str(sender_id_obj or "")
            chat_id = getattr(message, "chat_id", "")

            # Add THUMBSUP reaction immediately
            self._add_reaction(message_id, self._reaction_emoji)

            # Forward to Hub via TCP (atomic send+receive to prevent response interleaving)
            def forward():
                response = None
                try:
                    with self._send_lock:
                        future = asyncio.run_coroutine_threadsafe(
                            self._send_with_reconnect({
                                "channel": self.channel,
                                "bot": self.bot,
                                "sender_id": sender_id,
                                "chat_id": chat_id,
                                "content": text,
                                "message_id": message_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }),
                            self._conn_loop,
                        )
                        response = future.result(timeout=300)
                except Exception as e:
                    logger.error("Failed to forward message after retries: {}, exiting process", e)
                    os._exit(1)

                try:
                    if response and response.success and response.content:
                        self._send_text_reply(chat_id, message_id, response.content)
                    if response and response.success and response.metadata.get("done_emoji"):
                        self._add_reaction(message_id, response.metadata["done_emoji"])
                    elif response:
                        self._add_reaction(message_id, self._done_emoji)
                    self._remove_reaction(message_id)
                except Exception as e:
                    logger.error("Failed to send reply/reaction: {}", e)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("Feishu proxy message handler error: {}", e)

    def on_reaction(self, data: Any) -> None:
        """Handle reaction events (im.message.reaction.created_v1)."""
        pass

    def _add_reaction(self, message_id: str, emoji: str) -> None:
        """Add reaction emoji to message."""
        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji).build())
                    .build()
                )
                .build()
            )
            self._client.im.v1.message_reaction.create(request)
        except Exception as e:
            logger.debug("Failed to add reaction: {}", e)

    def _remove_reaction(self, message_id: str) -> None:
        """Remove reactions from message (best-effort)."""
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .build()
            )
            self._client.im.v1.message_reaction.delete(request)
        except Exception:
            pass

    def _send_text_reply(self, chat_id: str, root_id: str | None, content: str) -> None:
        """Send a text reply to the chat."""
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": content}))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if not resp.success():
                logger.warning("Failed to send reply: {} - {}", resp.code, resp.msg)
        except Exception as e:
            logger.error("Failed to send reply: {}", e)


def run_ws_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    client: Any, proxy_channel: FeishuProxyChannel | None = None,
    _ws_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Run the Feishu WebSocket connection in a dedicated thread."""
    import lark_oapi as lark

    domain = "https://open.feishu.cn" if config.get("domain", "feishu") == "feishu" else "https://open.larksuite.com"

    if proxy_channel is None:
        proxy_channel = FeishuProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot, client)

    builder = (
        lark.EventDispatcherHandler.builder(
            config.get("encryptKey", "") or "",
            config.get("verificationToken", "") or "",
        )
        .register_p2_im_message_receive_v1(proxy_channel.on_message)
        .register_p2_im_message_reaction_created_v1(proxy_channel.on_reaction)
    )
    event_handler = builder.build()

    ws_client = lark.ws.Client(
        config["appId"],
        config["appSecret"],
        domain=domain,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    def run_ws(ws_loop: asyncio.AbstractEventLoop | None) -> None:
        import lark_oapi.ws as _lark_ws

        logger.info("Feishu WS loop starting, connecting to {}...", domain)
        if ws_loop is None or ws_loop.is_closed():
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
        _lark_ws.client.loop = ws_loop

        try:
            logger.info("Feishu WS: calling client.start()...")
            ws_client.start()
            logger.info("Feishu WS: client.start() returned (should not happen in normal operation)")
        except Exception as e:
            logger.error("Feishu WS error: {}", e)
        finally:
            if ws_loop is not None and not ws_loop.is_closed():
                ws_loop.close()
            logger.info("Feishu WS loop ended")

    thread = threading.Thread(target=run_ws, args=(_ws_loop,), daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("appId") or not config.get("appSecret"):
        logger.error("Feishu proxy: appId and appSecret required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("Feishu proxy starting for {}:{}", channel, bot)

    # Create Feishu client for sending replies
    import lark_oapi as lark
    domain = "https://open.feishu.cn" if config.get("domain", "feishu") == "feishu" else "https://open.larksuite.com"
    client = (
        lark.Client.builder()
        .app_id(config["appId"])
        .app_secret(config["appSecret"])
        .domain(domain)
        .log_level(lark.LogLevel.INFO)
        .build()
    )

    # Connect to Hub via TCP in a dedicated thread with persistent loop
    try:
        proxy_channel = FeishuProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot, client)
        proxy_channel._connect_tcp()  # sync, blocks until connected
        logger.info("Registered with Hub via TCP")

        # Run WebSocket in a separate thread with its own loop
        run_ws_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, client, proxy_channel, None)
    except Exception as e:
        logger.error("Failed to register with Hub via TCP: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Feishu proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
