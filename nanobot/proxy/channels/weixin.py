"""WeChat (personal) proxy - runs as a separate process, polls WeChat HTTP API and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import sys
import time
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class WeixinProxyChannel(BaseProxyChannel):
    """Polls WeChat API and forwards messages to Hub via TCP."""

    CHANNEL_NAME = "WeChat"
    REQUIRED_CONFIG_FIELDS = []

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._send_reply_fn: Any = None

    def start(self) -> None:
        """Poll WeChat API and forward messages to Hub."""
        import httpx

        base_url = self.config.get("api_url", "https://ilinkai.weixin.qq.com")
        token = self.config.get("token", "")

        def fetch_updates():
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(
                        f"{base_url}/cgi-bin/getupdates",
                        params={"token": token, "_": int(time.time())},
                    )
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as e:
                logger.warning("WeChat getupdates error: {}", e)
            return None

        def send_reply(chat_id: str, content: str) -> None:
            try:
                import httpx
                with httpx.Client(timeout=30) as client:
                    client.post(
                        f"{base_url}/cgi-bin/sendmessage",
                        params={"token": token},
                        json={"chat_id": chat_id, "text": content},
                    )
            except Exception as e:
                logger.error("WeChat reply error: {}", e)

        self._send_reply_fn = send_reply

        while True:
            try:
                data = fetch_updates()
                if data:
                    for item in data.get("list", []):
                        msg_id = item.get("id", "")
                        if self.check_duplicate(msg_id):
                            continue

                        content = item.get("content", {}).get("text", "") or item.get("text", "")
                        sender_id = item.get("fromusername", "")
                        chat_id = item.get("chat_id", sender_id)

                        msg_data = self.build_message(sender_id, chat_id, content, msg_id)
                        response = self.send_to_hub(msg_data)

                        if response and response.success and response.content:
                            self._send_reply_fn(chat_id, response.content)
            except Exception as e:
                logger.warning("WeChat poll error: {}", e)
            time.sleep(3)


def main() -> None:
    WeixinProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WeChat proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
