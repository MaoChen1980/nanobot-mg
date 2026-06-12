"""Mochat proxy - runs as a separate process, connects to Mochat server via Socket.IO and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import json
import sys
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


def normalize_mochat_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


class MochatProxyChannel(BaseProxyChannel):
    """Handles Mochat message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "Mochat"
    REQUIRED_CONFIG_FIELDS = ["claw_token"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._socket: Any = None

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
                if not message_id or self.check_duplicate(message_id):
                    continue

                content = normalize_mochat_content(event_payload.get("content")) or "[empty message]"
                author = event_payload.get("author", "")
                group_id = event_payload.get("groupId", "")

                msg_data = self.build_message(author, target_id, content, message_id)
                self.send_to_hub(msg_data)

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

    def _process_send(self, item: dict) -> None:
        """Send queued message to Mochat."""
        self._send_reply(
            target_id=item["target_id"],
            content=item["content"],
            reply_to=item.get("reply_to", ""),
            group_id=item.get("group_id", ""),
        )

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Mochat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({
                "target_id": chat_id,
                "content": content,
                "reply_to": "",
                "group_id": "",
            })

    def start(self) -> None:
        """Run the Mochat Socket.IO connection."""
        import socketio

        base_url = self.config.get("base_url", "https://mochat.io").strip().rstrip("/")
        socket_url = self.config.get("socket_url", base_url)
        socket_path = self.config.get("socket_path", "/socket.io")
        claw_token = self.config.get("claw_token", "")

        serializer = "msgpack" if not self.config.get("socket_disable_msgpack", False) else "json"

        sio_client = socketio.Client(
            reconnection=True,
            reconnection_delay=1.0,
            reconnection_delay_max=10.0,
            logger=False, engineio_logger=False,
            serializer=serializer,
        )

        self._socket = sio_client

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
            self._on_socket_event("claw.session.events", payload)

        @sio_client.on("claw.panel.events")
        def on_panel_events(payload: dict[str, Any]) -> None:
            self._on_socket_event("claw.panel.events", payload)

        def _on_subscribe_result(data: Any) -> None:
            if isinstance(data, dict) and not data.get("result"):
                logger.warning("Mochat subscribe result: {}", data)

        sio_client.connect(
            socket_url,
            transports=["websocket"],
            socketio_path=socket_path.lstrip("/"),
            auth={"token": claw_token},
            wait_timeout=10,
        )
        sio_client.wait()


def main() -> None:
    MochatProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Mochat proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
