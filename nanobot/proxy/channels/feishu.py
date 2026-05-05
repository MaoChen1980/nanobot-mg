"""Feishu proxy - runs as a separate process, connects to Feishu WebSocket and forwards messages to nanobot Hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import threading
from typing import Any

import requests
from loguru import logger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feishu proxy - connects to Feishu and forwards messages to Hub")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    """Get channel config from environment variable (set by ProxyManager)."""
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


def _register(hub_url: str, channel: str, bot: str, pid: int) -> dict[str, Any]:
    """Register this proxy with the Hub."""
    payload = {
        "channel": channel,
        "bot": bot,
        "pid": pid,
        "heartbeat_interval": 30,
    }
    resp = requests.post(
        f"{hub_url}/api/proxy/register",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _send_heartbeat(hub_url: str, channel: str, bot: str) -> None:
    """Send heartbeat to Hub."""
    try:
        requests.post(
            f"{hub_url}/api/proxy/heartbeat",
            json={"channel": channel, "bot": bot},
            timeout=5,
        )
    except Exception as e:
        logger.warning("Heartbeat to Hub failed: {}", e)


def _send_message(hub_url: str, msg: dict[str, Any]) -> dict[str, Any]:
    """Send message to Hub and return response."""
    resp = requests.post(
        f"{hub_url}/api/proxy/message",
        json=msg,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


class FeishuProxyChannel:
    """Handles Feishu message events and forwards to Hub."""

    def __init__(self, config: dict, hub_url: str, channel: str, bot: str, client: Any):
        self.config = config
        self.hub_url = hub_url
        self.channel = channel
        self.bot = bot
        self._client = client
        self._processed: set[str] = set()
        self._reaction_emoji = config.get("react_emoji", "THUMBSUP")
        self._done_emoji = config.get("done_emoji")

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

            # Forward to Hub
            def forward():
                try:
                    response = _send_message(self.hub_url, {
                        "channel": self.channel,
                        "bot": self.bot,
                        "sender_id": sender_id,
                        "chat_id": chat_id,
                        "content": text,
                        "message_id": message_id,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    if response.get("success") and response.get("content"):
                        self._send_text_reply(chat_id, message_id, response["content"])
                    if response.get("success") and response.get("metadata", {}).get("done_emoji"):
                        self._add_reaction(message_id, response["metadata"]["done_emoji"])
                    else:
                        self._add_reaction(message_id, self._done_emoji)
                    # Remove THUMBSUP
                    self._remove_reaction(message_id)
                except Exception as e:
                    logger.warning("Failed to forward message: {}", e)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("Feishu proxy message handler error: {}", e)

    def on_reaction(self, data: Any) -> None:
        """Handle reaction events (im.message.reaction.created_v1)."""
        # No action needed - reaction events are informational only
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


def run_ws_loop(config: dict, hub_url: str, channel: str, bot: str, client: Any) -> None:
    """Run the Feishu WebSocket connection in a dedicated thread."""
    import lark_oapi as lark

    domain = "https://open.feishu.cn" if config.get("domain", "feishu") == "feishu" else "https://open.larksuite.com"

    proxy_channel = FeishuProxyChannel(config, hub_url, channel, bot, client)

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

    def run_ws():
        import lark_oapi.ws as _lark_ws

        logger.info("Feishu WS loop starting, connecting to {}...", domain)
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
            ws_loop.close()
            logger.info("Feishu WS loop ended")

    thread = threading.Thread(target=run_ws, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("appId") or not config.get("appSecret"):
        logger.error("Feishu proxy: appId and appSecret required in config")
        sys.exit(1)

    hub_url = args.hub_url
    channel = args.channel
    bot = args.bot

    logger.info("Feishu proxy starting for {}:{}", channel, bot)

    # Register with Hub
    try:
        result = _register(hub_url, channel, bot, os.getpid())
        logger.info("Registered with Hub: {}", result)
    except Exception as e:
        logger.error("Failed to register with Hub: {}", e)
        sys.exit(1)

    # Start heartbeat thread - first heartbeat fires immediately after registration
    def heartbeat_loop():
        _send_heartbeat(hub_url, channel, bot)  # immediate first heartbeat
        hb_count = 0
        while True:
            time.sleep(20)
            hb_count += 1
            _send_heartbeat(hub_url, channel, bot)
            if hb_count % 10 == 0:
                logger.info("Heartbeat #{} sent for {}:{}", hb_count, channel, bot)

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

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

    # Run WebSocket loop
    run_ws_loop(config, hub_url, channel, bot, client)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Feishu proxy crashed: {}", traceback.format_exc())
        sys.exit(1)