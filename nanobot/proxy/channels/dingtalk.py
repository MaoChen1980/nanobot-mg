"""DingTalk proxy - runs as a separate process, connects to DingTalk Stream SDK and forwards messages to nanobot Hub via TCP."""

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
    parser = argparse.ArgumentParser(description="DingTalk proxy - connects to DingTalk Stream SDK and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    """Get channel config from environment variable (set by ProxyManager)."""
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class DingTalkProxyChannel:
    """Handles DingTalk message events and forwards to Hub via TCP."""

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str, client: Any):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._client = client
        self._processed: dict[str, float] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()

    def _connect_tcp(self) -> None:
        """Connect to Hub via TCP in a dedicated thread with persistent loop."""
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
        """Send message to Hub via TCP and wait for response."""
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
        """Sync callback from DingTalk SDK - forward message to Hub."""
        logger.info("DingTalk on_message called")
        try:
            from dingtalk_stream.chatbot import ChatbotMessage

            chatbot_msg = ChatbotMessage.from_dict(data)

            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            elif chatbot_msg.extensions.get("content", {}).get("recognition"):
                content = chatbot_msg.extensions["content"]["recognition"].strip()
            if not content:
                content = data.get("text", {}).get("content", "").strip()

            if not content:
                logger.warning("Received empty or unsupported message type: {}", chatbot_msg.message_type)
                return

            msg_id = chatbot_msg.message_id or ""
            now = time.time()
            if msg_id in self._processed:
                return
            self._processed[msg_id] = now
            self._processed = {k: v for k, v in self._processed.items() if now - v < 300}

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id or "unknown"
            conversation_type = data.get("conversationType")
            conversation_id = data.get("conversationId") or data.get("openConversationId")
            is_group = conversation_type == "2" and conversation_id
            chat_id = f"group:{conversation_id}" if is_group else sender_id

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
                                "content": content,
                                "message_id": msg_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }),
                            self._conn_loop,
                        )
                        response = future.result(timeout=300)

                    if response and response.success and response.content:
                        self._send_reply(chat_id, sender_id, is_group, response.content)
                except Exception as e:
                    logger.error("Failed to forward message after retries: {}, exiting process", e)
                    os._exit(1)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("DingTalk proxy message handler error: {}", e)

    def _send_reply(self, chat_id: str, sender_id: str, is_group: bool, content: str) -> None:
        """Send a text reply via DingTalk API."""
        try:
            import httpx
            token = self._get_access_token()
            if not token:
                return

            if is_group:
                url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
                payload = {
                    "robotCode": self.config.get("client_id", ""),
                    "openConversationId": chat_id,
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"text": content, "title": "Nanobot Reply"}, ensure_ascii=False),
                }
            else:
                url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
                payload = {
                    "robotCode": self.config.get("client_id", ""),
                    "userIds": [sender_id],
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"text": content, "title": "Nanobot Reply"}, ensure_ascii=False),
                }

            headers = {"x-acs-dingtalk-access-token": token}
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    logger.warning("DingTalk reply failed: {} - {}", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("DingTalk reply error: {}", e)

    def _get_access_token(self) -> str | None:
        """Get DingTalk access token."""
        try:
            import httpx
            url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
            data = {
                "appKey": self.config.get("client_id", ""),
                "appSecret": self.config.get("client_secret", ""),
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=data)
                if resp.status_code == 200:
                    return resp.json().get("accessToken")
        except Exception:
            pass
        return None


def run_stream_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: DingTalkProxyChannel,
) -> None:
    """Run the DingTalk Stream connection in a dedicated thread."""
    from dingtalk_stream import CallbackHandler, DingTalkStreamClient, Credential
    from dingtalk_stream.chatbot import ChatbotMessage

    credential = Credential(config.get("client_id", ""), config.get("client_secret", ""))
    stream_client = DingTalkStreamClient(credential)

    class Handler(CallbackHandler):
        async def process(self, message):
            proxy_channel.on_message(message.data)
            return 0, "OK"

    stream_client.register_callback_handler(ChatbotMessage.TOPIC, Handler())

    def run_stream() -> None:
        while True:
            try:
                stream_client.start()
            except Exception as e:
                logger.error("DingTalk stream error: {}", e)
                time.sleep(5)

    thread = threading.Thread(target=run_stream, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("client_id") or not config.get("client_secret"):
        logger.error("DingTalk proxy: client_id and client_secret required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("DingTalk proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = DingTalkProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot, None)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")

        run_stream_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start DingTalk proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("DingTalk proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
