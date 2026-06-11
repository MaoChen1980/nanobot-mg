"""Microsoft Teams proxy - runs as a separate process, hosts HTTP webhook server and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class MSTeamsProxyChannel(BaseProxyChannel):
    """Handles Microsoft Teams message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "MSTeams"
    REQUIRED_CONFIG_FIELDS = ["app_id", "app_password"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._conversation_refs: dict[str, dict] = {}

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
            if self.check_duplicate(msg_id):
                return

            # Store conversation ref for replies
            self._conversation_refs[conversation_id] = {
                "service_url": service_url,
                "activity_id": activity_id,
            }

            msg_data = self.build_message(sender_id, conversation_id, text.strip(), activity_id)
            response = self.send_to_hub(msg_data)

            if response and response.success and response.content:
                self._enqueue_send({"chat_id": conversation_id, "content": response.content})

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

    def _process_send(self, item: dict) -> None:
        """Send queued message to MSTeams."""
        self._send_reply(item["chat_id"], item["content"])

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to MSTeams chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({"chat_id": chat_id, "content": content})

    def start(self) -> None:
        """Run the MSTeams webhook server."""
        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 3978)
        path = self.config.get("path", "/api/messages")

        channel_obj = self

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
        logger.info("MSTeams webhook listening on http://{}:{}{}", host, port, path)
        server.serve_forever()


def main() -> None:
    MSTeamsProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("MSTeams proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
