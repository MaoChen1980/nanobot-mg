"""Discord proxy - runs as a separate process, connects to Discord via discord.py and forwards messages to nanobot Hub via TCP."""

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

try:
    import discord
    from discord import Intents, app_commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discord proxy - connects to Discord and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class DiscordProxyChannel:
    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._processed_ids: set[str] = set()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()
        self._bot_user_id: str | None = None
        self._client: Any = None

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


    def on_message(self, message: Any) -> None:
        try:
            if self._bot_user_id and str(message.author.id) == self._bot_user_id:
                return

            msg_id = str(message.id)
            if msg_id in self._processed_ids:
                return
            self._processed_ids.add(msg_id)
            if len(self._processed_ids) > 1000:
                self._processed_ids = set(list(self._processed_ids)[-500:])

            sender_id = str(message.author.id)
            channel_id = str(message.channel.id)
            content = message.content or ""

            if not content:
                return

            def forward():
                try:
                    with self._send_lock:
                        future = asyncio.run_coroutine_threadsafe(
                            self._send_with_reconnect({
                                "channel": self.channel,
                                "bot": self.bot,
                                "sender_id": sender_id,
                                "chat_id": channel_id,
                                "content": content,
                                "message_id": msg_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }),
                            self._conn_loop,
                        )
                        response = future.result(timeout=300)

                    if response and response.success and response.content:
                        asyncio.run_coroutine_threadsafe(
                            message.channel.send(response.content),
                            self._conn_loop,
                        )
                except Exception as e:
                    logger.error("Failed to forward message after retries: {}, exiting process", e)
                    os._exit(1)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("Discord proxy message handler error: {}", e)


def run_discord_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: DiscordProxyChannel,
) -> None:
    intents = Intents.none()
    intents.value = config.get("intents", 37377)

    class BotClient(discord.Client):
        def __init__(self, proxy: DiscordProxyChannel, **kwargs):
            super().__init__(**kwargs)
            self._proxy = proxy
            self.tree = app_commands.CommandTree(self)

        async def on_ready(self):
            self._proxy._bot_user_id = str(self.user.id) if self.user else None
            logger.info("Discord proxy bot connected as {}", self._proxy._bot_user_id)

        async def on_message(self, message: discord.Message):
            self._proxy.on_message(message)

    proxy_channel._client = BotClient(proxy_channel, intents=intents)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy_channel._client.start(config.get("token", "")))
        except Exception as e:
            logger.error("Discord bot error: {}", e)
        finally:
            loop.close()

    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("token"):
        logger.error("Discord proxy: token required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("Discord proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = DiscordProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_discord_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start Discord proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Discord proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
