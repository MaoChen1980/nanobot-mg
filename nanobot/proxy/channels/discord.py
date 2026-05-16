"""Discord proxy - runs as a separate process, connects to Discord via discord.py and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel

try:
    import discord
    from discord import Intents, app_commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False


class DiscordProxyChannel(BaseProxyChannel):
    """Handles Discord message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "Discord"
    REQUIRED_CONFIG_FIELDS = ["token"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._bot_user_id: str | None = None
        self._client: Any = None
        self._bot_loop: asyncio.AbstractEventLoop | None = None

    def on_message(self, message: Any) -> None:
        try:
            if self._bot_user_id and str(message.author.id) == self._bot_user_id:
                return

            msg_id = str(message.id)
            if self.check_duplicate(msg_id):
                return

            sender_id = str(message.author.id)
            channel_id = str(message.channel.id)
            content = message.content or ""

            if not content:
                return

            msg_data = self.build_message(sender_id, channel_id, content, msg_id)
            response = self.send_to_hub(msg_data)

            if response and response.success and response.content:
                self._enqueue_send({"chat_id": channel_id, "content": response.content})

        except Exception as e:
            logger.error("Discord proxy message handler error: {}", e)

    def _process_send(self, item: dict) -> None:
        """Send queued message to Discord via async bridge."""
        if not self._client or not self._bot_loop:
            return
        chat_id = item["chat_id"]
        content = item.get("content", "")
        if not content:
            return
        try:
            async def _send():
                channel = self._client.get_channel(int(chat_id))
                if channel:
                    await channel.send(content)
            future = asyncio.run_coroutine_threadsafe(_send(), self._bot_loop)
            future.result(timeout=30)
        except Exception as e:
            logger.error("Discord send error: {}", e)

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Discord channel."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({"chat_id": chat_id, "content": content})

    def start(self) -> None:
        """Run the Discord bot connection."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._bot_loop = loop

        intents = Intents.none()
        intents.value = self.config.get("intents", 37377)

        _channel = self

        class BotClient(discord.Client):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.tree = app_commands.CommandTree(self)

            async def on_ready(self):
                _channel._bot_user_id = str(self.user.id) if self.user else None
                logger.info("Discord proxy bot connected as {}", _channel._bot_user_id)

            async def on_message(self, message: discord.Message):
                _channel.on_message(message)

        self._client = BotClient(intents=intents)
        loop.run_until_complete(self._client.start(self.config.get("token", "")))


def main() -> None:
    DiscordProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Discord proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
