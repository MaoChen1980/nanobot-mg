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

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to WeChat chat."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({"chat_id": chat_id, "content": content})

    def _process_send(self, item: dict) -> None:
        """Send queued message to WeChat via HTTP."""
        import httpx
        base_url = self.config.get("api_url", "https://ilinkai.weixin.qq.com")
        token = self.config.get("token", "")
        try:
            payload: dict[str, Any] = {"chat_id": item["chat_id"], "text": item["content"]}
            media_paths = self._scan_media_paths(item["content"])
            if media_paths:
                path, mtype = media_paths[0]
                if mtype == "image":
                    payload["image"] = path
            with httpx.Client(timeout=30) as client:
                client.post(
                    f"{base_url}/cgi-bin/sendmessage",
                    params={"token": token},
                    json=payload,
                )
        except Exception as e:
            logger.error("WeChat send error: {}", e)

    def start(self) -> None:
        """Poll WeChat API and forward messages to Hub."""
        import httpx

        base_url = self.config.get("api_url", "https://ilinkai.weixin.qq.com")
        if "ilinkai" in base_url.lower():
            logger.warning("Weixin: default api_url points to iLinkAI bridge, not official WeChat API")
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

        while True:
            try:
                data = fetch_updates()
                if data:
                    for item in data.get("list", []):
                        msg_id = item.get("id", "")
                        if self.check_duplicate(msg_id):
                            continue

                        msg_type = item.get("type", "text")
                        content_data = isinstance(item.get("content"), dict) and item["content"]
                        content = content_data.get("text", "") if content_data else item.get("text", "")
                        sender_id = item.get("fromusername", "")
                        chat_id = item.get("chat_id", sender_id)

                        media_items: list[str] = []

                        if msg_type == "image":
                            url = content_data.get("url") if content_data else None
                            if url:
                                try:
                                    resp = httpx.get(url, timeout=30)
                                    if resp.status_code == 200:
                                        ct = resp.headers.get("content-type", "")
                                        ext_map = {"jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp", "png": ".png", "bmp": ".bmp"}
                                        ext = ".png"
                                        for key, val in ext_map.items():
                                            if key in ct:
                                                ext = val
                                                break
                                        filename = f"weixin_img_{msg_id}{ext}"
                                        local_path = self._save_media_bytes(filename, resp.content)
                                        media_items.append(local_path)
                                        content = self._media_text_reference(local_path)
                                except Exception as e:
                                    logger.warning("WeChat download image error: {}", e)

                        elif msg_type == "file":
                            url = content_data.get("url") if content_data else None
                            name = content_data.get("name", f"weixin_file_{msg_id}") if content_data else f"weixin_file_{msg_id}"
                            if url:
                                try:
                                    resp = httpx.get(url, timeout=30)
                                    if resp.status_code == 200:
                                        local_path = self._save_media_bytes(name, resp.content)
                                        media_items.append(local_path)
                                        content = self._media_text_reference(local_path)
                                except Exception as e:
                                    logger.warning("WeChat download file error: {}", e)

                        msg_data = self.build_message(sender_id, chat_id, content, msg_id, media=media_items)
                        self.send_to_hub(msg_data)
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
