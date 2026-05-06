"""Microsoft Teams proxy - runs as a separate process, hosts HTTP webhook server and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loguru import logger

from nanobot.proxy.protocol import HubResponse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Microsoft Teams proxy - hosts webhook server and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class MSTeamsProxyChannel:
    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        self.config = config
        self.hub_tcp_host = hub_tcp_host
        self.hub_tcp_port = hub_tcp_port
        self.channel = channel
        self.bot = bot
        self._processed: set[str] = set()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = threading.Lock()
        self._conversation_refs: dict[str, dict] = {}
        self._http: Any = None

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


    def _on_activity(self, activity: dict[str, Any]) -> None:
        try:
            if activity.get("type") != "message":
                return

            conversation = activity.get("conversation") or {}
            from_user = activity.get("from") or {}

            sender_id = str(from_user.get("aadObjectId") or from_user.get("id") or "").strip()
            conversation_id = str(conversation.get("id") or "").strip()
            service_url = str(activity.get("serviceUrl") or "").strip()
            activity_id = str(activity.get("id") or "").strip()

            if not sender_id or not conversation_id:
                return

            # Skip self-messages
            recipient = activity.get("recipient") or {}
            if from_user.get("id") == recipient.get("id"):
                return

            text = activity.get("text") or ""
            if not text:
                return

            msg_id = f"{conversation_id}:{activity_id}"
            if msg_id in self._processed:
                return
            self._processed.add(msg_id)
            if len(self._processed) > 1000:
                self._processed = set(list(self._processed)[-500:])

            # Store conversation ref for replies
            self._conversation_refs[conversation_id] = {
                "service_url": service_url,
                "activity_id": activity_id,
            }

            def forward():
                try:
                    with self._send_lock:
                        future = asyncio.run_coroutine_threadsafe(
                            self._send_with_reconnect({
                                "channel": self.channel,
                                "bot": self.bot,
                                "sender_id": sender_id,
                                "chat_id": conversation_id,
                                "content": text.strip(),
                                "message_id": activity_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }),
                            self._conn_loop,
                        )
                        response = future.result(timeout=300)

                    if response and response.success and response.content:
                        self._send_reply(conversation_id, response.content)
                except Exception as e:
                    logger.error("Failed to forward message after retries: {}, exiting process", e)
                    os._exit(1)

            t = threading.Thread(target=forward, daemon=True)
            t.start()

        except Exception as e:
            logger.error("MSTeams proxy handler error: {}", e)

    def _send_reply(self, conversation_id: str, content: str) -> None:
        try:
            import httpx
            ref = self._conversation_refs.get(conversation_id)
            if not ref:
                return

            base_url = ref["service_url"].rstrip("/")
            token = self.config.get("access_token", "")

            with httpx.Client(timeout=30) as client:
                client.post(
                    f"{base_url}/v3/conversations/{conversation_id}/activities",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"type": "message", "text": content},
                )
        except Exception as e:
            logger.error("MSTeams reply error: {}", e)


def run_msteams_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: MSTeamsProxyChannel,
) -> None:
    host = config.get("host", "0.0.0.0")
    port = config.get("port", 3978)
    path = config.get("path", "/api/messages")

    channel_obj = proxy_channel

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != path:
                self.send_response(404)
                self.end_headers()
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8"))
            except Exception as e:
                logger.warning("MSTeams invalid request: {}", e)
                self.send_response(400)
                self.end_headers()
                return

            try:
                channel_obj._on_activity(payload)
            except Exception as e:
                logger.warning("MSTeams activity error: {}", e)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info("MSTeams webhook listening on http://{}:{}{}", host, port, path)

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("app_id") or not config.get("app_password"):
        logger.error("MSTeams proxy: app_id and app_password required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("MSTeams proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = MSTeamsProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_msteams_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start MSTeams proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("MSTeams proxy crashed: {}", traceback.format_exc())
        sys.exit(1)