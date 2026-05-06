"""Mochat proxy - runs as a separate process, connects to Mochat server via Socket.IO and forwards messages to nanobot Hub via TCP."""

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
    parser = argparse.ArgumentParser(description="Mochat proxy - connects to Mochat server and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


def normalize_mochat_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


class MochatProxyChannel:
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
        self._socket: Any = None
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


    def _on_socket_event(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            if event_type not in ("claw.session.events", "claw.panel.events"):
                return

            events = payload.get("events", [])
            target_id = payload.get("sessionId") or payload.get("panelId", "")
            if not events or not target_id:
                return

            for event in events:
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "message.add":
                    continue

                event_payload = event.get("payload", {})
                message_id = event_payload.get("messageId", "")
                if not message_id or message_id in self._processed:
                    continue
                self._processed.add(message_id)
                if len(self._processed) > 1000:
                    self._processed = set(list(self._processed)[-500:])

                content = normalize_mochat_content(event_payload.get("content")) or "[empty message]"
                author = event_payload.get("author", "")
                group_id = event_payload.get("groupId", "")

                def forward():
                    try:
                        with self._send_lock:
                            future = asyncio.run_coroutine_threadsafe(
                                self._send_with_reconnect({
                                    "channel": self.channel,
                                    "bot": self.bot,
                                    "sender_id": author,
                                    "chat_id": target_id,
                                    "content": content,
                                    "message_id": message_id,
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                }),
                                self._conn_loop,
                            )
                            response = future.result(timeout=300)

                        if response and response.success and response.content:
                            self._send_reply(target_id, content, message_id, group_id)
                    except Exception as e:
                        logger.error("Failed to forward message after retries: {}, exiting process", e)
                        os._exit(1)

                t = threading.Thread(target=forward, daemon=True)
                t.start()

        except Exception as e:
            logger.error("Mochat proxy event handler error: {}", e)

    def _send_reply(self, target_id: str, content: str, reply_to: str, group_id: str) -> None:
        try:
            import httpx
            base_url = self.config.get("base_url", "https://mochat.io").strip().rstrip("/")
            claw_token = self.config.get("claw_token", "")

            is_panel = not target_id.startswith("session_")
            path = "/api/claw/groups/panels/send" if is_panel else "/api/claw/sessions/send"
            id_key = "panelId" if is_panel else "sessionId"

            body = {id_key: target_id, "content": content}
            if reply_to:
                body["replyTo"] = reply_to
            if group_id:
                body["groupId"] = group_id

            with httpx.Client(timeout=30) as client:
                client.post(
                    f"{base_url}{path}",
                    headers={"Content-Type": "application/json", "X-Claw-Token": claw_token},
                    json=body,
                )
        except Exception as e:
            logger.error("Mochat reply error: {}", e)


def run_mochat_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: MochatProxyChannel,
) -> None:
    import socketio

    base_url = config.get("base_url", "https://mochat.io").strip().rstrip("/")
    socket_url = config.get("socket_url", base_url)
    socket_path = config.get("socket_path", "/socket.io")
    claw_token = config.get("claw_token", "")

    serializer = "msgpack" if not config.get("socket_disable_msgpack", False) else "json"

    sio_client = socketio.Client(
        reconnection=True,
        reconnection_delay=1.0,
        reconnection_delay_max=10.0,
        logger=False, engineio_logger=False,
        serializer=serializer,
    )

    proxy_channel._socket = sio_client

    @sio_client.event
    def connect() -> None:
        logger.info("Mochat Socket.IO connected")
        sio_client.emit("com.claw.im.subscribeSessions", {
            "sessionIds": [], "cursors": {}, "limit": 100,
        }, callback=_on_subscribe_result)

    @sio_client.event
    def disconnect() -> None:
        logger.warning("Mochat Socket.IO disconnected")

    @sio_client.on("claw.session.events")
    def on_session_events(payload: dict[str, Any]) -> None:
        proxy_channel._on_socket_event("claw.session.events", payload)

    @sio_client.on("claw.panel.events")
    def on_panel_events(payload: dict[str, Any]) -> None:
        proxy_channel._on_socket_event("claw.panel.events", payload)

    def _on_subscribe_result(data: Any) -> None:
        if isinstance(data, dict) and not data.get("result"):
            logger.warning("Mochat subscribe result: {}", data)

    def run_socket() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            sio_client.connect(
                socket_url,
                transports=["websocket"],
                socketio_path=socket_path.lstrip("/"),
                auth={"token": claw_token},
                wait_timeout=10,
            )
            sio_client.wait()
        except Exception as e:
            logger.error("Mochat Socket.IO connection error: {}", e)
        finally:
            loop.close()

    thread = threading.Thread(target=run_socket, daemon=True)
    thread.start()

    while True:
        time.sleep(5)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    if not config.get("claw_token"):
        logger.error("Mochat proxy: claw_token required in config")
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("Mochat proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = MochatProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_mochat_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start Mochat proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Mochat proxy crashed: {}", traceback.format_exc())
        sys.exit(1)