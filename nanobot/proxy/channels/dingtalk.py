"""DingTalk proxy - runs as a separate process, connects to DingTalk Stream SDK and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel

# Supported image extensions for local file detection
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
# Supported file extensions (any file can be sent)
FILE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".zip", ".rar", ".mp3", ".mp4", ".csv", ".json", ".xml",
}


class DingTalkProxyChannel(BaseProxyChannel):
    """Handles DingTalk message events and forwards to Hub via TCP."""

    CHANNEL_NAME = "DingTalk"
    REQUIRED_CONFIG_FIELDS = ["clientId", "clientSecret"]

    # ------------------------------------------------------------------
    # Media upload/download
    # ------------------------------------------------------------------

    def _upload_media(self, file_path: str, file_type: str = "image") -> str | None:
        """Upload a local file to DingTalk and return media_id.

        Args:
            file_path: Path to the local file
            file_type: 'image' or 'file'

        Returns:
            media_id on success, None on failure
        """
        if not os.path.exists(file_path):
            logger.warning(f"File not found for upload: {file_path}")
            return None

        try:
            token = self._get_access_token()
            if not token:
                return None

            # Determine file extension and mime type
            ext = Path(file_path).suffix.lower()

            # API type: image for pictures, file for other files
            api_type = "image" if ext in IMAGE_EXTENSIONS else "file"

            with open(file_path, "rb") as f:
                file_data = f.read()

            file_size = len(file_data)
            if file_size > 20 * 1024 * 1024:  # 20MB limit
                logger.warning(f"File too large: {file_path} ({file_size} bytes)")
                return None

            # Upload via DingTalk media API
            import httpx

            url = "https://api.dingtalk.com/v1.0/robot/media/upload"
            headers = {
                "x-acs-dingtalk-access-token": token,
            }

            files = {
                "media": (os.path.basename(file_path), file_data, self._get_mime_type(ext)),
            }
            data = {"file_type": api_type}

            with httpx.Client(timeout=60) as client:
                resp = client.post(url, files=files, data=data, headers=headers)

            if resp.status_code == 200:
                result = resp.json()
                media_id = result.get("mediaId") or result.get("media_id")
                if media_id:
                    logger.info(f"Uploaded {file_path} -> media_id: {media_id}")
                    return media_id
                else:
                    logger.warning(f"Upload response missing media_id: {result}")
            else:
                logger.warning(f"Upload failed: {resp.status_code} - {resp.text[:200]}")

        except Exception as e:
            logger.exception(f"Failed to upload media: {file_path}")
        return None

    def _get_mime_type(self, ext: str) -> str:
        """Get MIME type from file extension."""
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".txt": "text/plain",
            ".zip": "application/zip",
            ".csv": "text/csv",
        }
        return mime_map.get(ext.lower(), "application/octet-stream")

    def _download_media(self, download_code: str, media_type: str = "image") -> str | None:
        """Download media from DingTalk using downloadCode.

        Args:
            download_code: The download code from the message
            media_type: 'image' or 'file'

        Returns:
            Local file path on success, None on failure
        """
        try:
            token = self._get_access_token()
            if not token:
                return None

            import httpx

            url = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
            headers = {
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            }
            payload = {"downloadCode": download_code}

            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                # Determine file extension from Content-Type
                content_type = resp.headers.get("Content-Type", "")
                ext = self._guess_extension_from_mime(content_type)

                # Save to temp directory
                temp_dir = Path.home() / ".nanobot" / "media" / "incoming"
                temp_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{uuid.uuid4().hex[:12]}{ext}"
                dest = temp_dir / filename
                dest.write_bytes(resp.content)

                logger.info(f"Downloaded {media_type} to {dest}")
                return str(dest)
            else:
                logger.warning(f"Download failed: {resp.status_code} - {resp.text[:200]}")

        except Exception as e:
            logger.exception(f"Failed to download media: {download_code}")
        return None

    def _guess_extension_from_mime(self, mime_type: str) -> str:
        """Guess file extension from MIME type."""
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "text/plain": ".txt",
            "application/zip": ".zip",
            "application/octet-stream": ".bin",
        }
        return mime_to_ext.get(mime_type.lower(), ".bin")

    # ------------------------------------------------------------------
    # Content parsing and media detection
    # ------------------------------------------------------------------

    def _detect_and_upload_media(self, content: str) -> tuple[str, list[str]]:
        """Parse content for local media paths, upload them, and return cleaned content + media_ids.

        Returns:
            (cleaned_content, list of media_id strings)
        """
        media_ids: list[str] = []
        cleaned = content

        # Pattern 1: Markdown image syntax ![alt](path)
        md_image_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"

        def upload_md_image(match):
            alt_text = match.group(1) or "image"
            path = match.group(2)
            # Skip HTTP URLs
            if path.startswith("http://") or path.startswith("https://"):
                return match.group(0)
            # Upload local file
            media_id = self._upload_media(path, "image")
            if media_id:
                media_ids.append(media_id)
                return f"![{alt_text}]({media_id})"
            logger.warning(f"Failed to upload image: {path}")
            return match.group(0)

        cleaned = re.sub(md_image_pattern, upload_md_image, cleaned)

        # Pattern 2: File markers [DINGTALK_FILE]{"path": "...", "name": "..."}[/DINGTALK_FILE]
        file_marker_pattern = r"\[DINGTALK_FILE\]\s*(\{[^}]+\})\s*\[/DINGTALK_FILE\]"

        def upload_file_marker(match):
            try:
                file_info = json.loads(match.group(1))
                file_path = file_info.get("path", "")
                if not file_path:
                    return match.group(0)
                media_id = self._upload_media(file_path, "file")
                if media_id:
                    media_ids.append(media_id)
                    # Replace with just the marker text (file will be sent separately)
                    file_name = file_info.get("name", os.path.basename(file_path))
                    return f"[文件: {file_name}]"
            except json.JSONDecodeError:
                pass
            return match.group(0)

        cleaned = re.sub(file_marker_pattern, upload_file_marker, cleaned)

        # Pattern 3: Bare local file paths (detect common patterns)
        # Match paths that look like local files (not URLs)
        bare_path_pattern = r"(?<!\w)([A-Za-z]:\\[^\s\\]+|/[^\s]+(?:\.[a-zA-Z0-9]+))(?!\w)"

        def process_bare_path(match):
            path = match.group(1)
            # Skip if it looks like a URL
            if "://" in path or path.startswith("http"):
                return match.group(0)
            # Skip if it's likely a command or path with special chars
            if any(c in path for c in ["&&", "||", "|", "`", "$"]):
                return match.group(0)
            ext = Path(path).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                media_id = self._upload_media(path, "image")
                if media_id:
                    media_ids.append(media_id)
                    return f"![image]({media_id})"
            elif ext:
                media_id = self._upload_media(path, "file")
                if media_id:
                    media_ids.append(media_id)
                    return f"[文件: {os.path.basename(path)}]"
            return match.group(0)

        cleaned = re.sub(bare_path_pattern, process_bare_path, cleaned)

        return cleaned, media_ids

    # ------------------------------------------------------------------
    # Send logic
    # ------------------------------------------------------------------

    def _process_send(self, item: dict) -> None:
        """Send queued message to DingTalk via HTTP."""
        import httpx

        try:
            token = self._get_access_token()
            if not token:
                logger.warning("DingTalk send skipped: no access token")
                return

            chat_id = item["chat_id"]
            sender_id = item.get("sender_id", "")
            is_group = item["is_group"]
            content = item["content"]
            media_ids = item.get("media_ids", [])  # Pre-uploaded media_ids from hub

            # Detect and upload local media files (if not already uploaded)
            if not media_ids:
                content, media_ids = self._detect_and_upload_media(content)

            # Build API URL and payload
            if is_group:
                url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
                payload_base = {
                    "robotCode": self.config.get("clientId", ""),
                    "openConversationId": chat_id,
                }
            else:
                url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
                payload_base = {
                    "robotCode": self.config.get("clientId", ""),
                    "userIds": [sender_id] if sender_id else [],
                }

            headers = {"x-acs-dingtalk-access-token": token}

            # First, send the text message (if any)
            if content.strip():
                payload = {
                    **payload_base,
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({
                        "title": "Nanobot Reply",
                        "text": content,
                    }, ensure_ascii=False),
                }
                with httpx.Client(timeout=30) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code >= 400:
                        logger.warning("DingTalk text send failed: {} - {}", resp.status_code, resp.text[:200])

            # Then send each media item
            for media_id in media_ids:
                self._send_media(chat_id, is_group, media_id, headers, url, payload_base)

        except Exception as e:
            logger.error("DingTalk send error: {}", e)

    def _send_media(self, chat_id: str, is_group: bool, media_id: str, headers: dict, url: str, payload_base: dict) -> None:
        """Send a media message (image or file) via DingTalk API."""
        import httpx

        try:
            payload = {
                **payload_base,
                "msgKey": "sampleImage",
                "msgParam": json.dumps({"imageId": media_id}, ensure_ascii=False),
            }

            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    logger.warning("DingTalk media send failed: {} - {}", resp.status_code, resp.text[:200])
                else:
                    logger.info(f"Sent media {media_id} to {chat_id}")

        except Exception as e:
            logger.error("DingTalk media send error: {}", e)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    def on_message(self, data: Any) -> None:
        """Sync callback from DingTalk SDK - forward message to Hub."""
        logger.info("DingTalk on_message called, msgType: {}", data.get("msgType", "unknown"))
        try:
            from dingtalk_stream.chatbot import ChatbotMessage

            chatbot_msg = ChatbotMessage.from_dict(data)
            msg_type = data.get("msgType", "text")
            content = ""
            media: list[str] = []

            # Handle different message types
            if msg_type in ("text", "sampleText"):
                if chatbot_msg.text:
                    content = chatbot_msg.text.content.strip()
                else:
                    text_data = data.get("text", {})
                    content = text_data.get("content", "").strip() if isinstance(text_data, dict) else ""

            elif msg_type in ("picture", "image", "sampleImage"):
                # Image message - download the image
                pic_data = data.get("picture", {}) or data.get("image", {})
                download_code = pic_data.get("downloadCode") if isinstance(pic_data, dict) else None
                if download_code:
                    content = "[用户发送了图片]"
                    local_path = self._download_media(download_code, "image")
                    if local_path:
                        content = f"[用户发送了图片: {local_path}]"
                        media.append(local_path)
                else:
                    content = "[收到图片消息]"

            elif msg_type in ("file", "sampleFile"):
                # File message - download the file
                file_data = data.get("file", {})
                download_code = file_data.get("downloadCode") if isinstance(file_data, dict) else None
                if download_code:
                    content = "[用户发送了文件]"
                    local_path = self._download_media(download_code, "file")
                    if local_path:
                        content = f"[用户发送了文件: {os.path.basename(local_path)}]"
                        media.append(local_path)
                else:
                    content = "[收到文件消息]"

            elif isinstance(chatbot_msg.extensions.get("content"), dict):
                # Voice/audio with recognition
                content = chatbot_msg.extensions["content"].get("recognition", "").strip()
                if not content:
                    content = "[收到语音消息]"

            else:
                logger.info("Unhandled message type: {}", msg_type)
                content = f"[收到 {msg_type} 类型消息]"

            if not content and not media:
                logger.warning("Received empty message: {}", msg_type)
                return

            msg_id = chatbot_msg.message_id or ""
            if self.check_duplicate(msg_id):
                return

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id or "unknown"
            conversation_type = data.get("conversationType")
            conversation_id = data.get("conversationId") or data.get("openConversationId")
            is_group = conversation_type == "2" and conversation_id
            chat_id = f"group:{conversation_id}" if is_group else sender_id

            # Build message with optional media
            msg_data = self.build_message(sender_id, chat_id, content, msg_id, media=media)
            response = self.send_to_hub(msg_data)

            if response and response.success and (response.content or response.media):
                self._send_reply(chat_id, sender_id, is_group, response.content, media=response.media)

        except Exception as e:
            logger.error("DingTalk proxy message handler error: {}", e)

    def _send_reply(self, chat_id: str, sender_id: str, is_group: bool, content: str, media: list[str] | None = None) -> None:
        """Queue a reply for ordered delivery."""
        content = re.sub(
            r"^\*\*Nanobot Reply\*\*\s*\n+",
            "",
            content,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if media:
            media_text = ""
            for path in media:
                ext = Path(path).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    media_text += f"\n![image]({path})"
                else:
                    name = os.path.basename(path)
                    media_text += f"\n[DINGTALK_FILE]{{\"path\": \"{path}\", \"name\": \"{name}\"}}[/DINGTALK_FILE]"
            content = (content or "") + media_text
        actual_id = chat_id[len("group:"):] if is_group else chat_id
        self._enqueue_send({
            "chat_id": actual_id,
            "sender_id": sender_id,
            "is_group": is_group,
            "content": content,
        })

    # ------------------------------------------------------------------
    # Push delivery from Hub (tool events, thinking, reminders, etc.)
    # ------------------------------------------------------------------

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Queue push delivery (non-blocking) so the background reader stays
        responsive while the send worker preserves FIFO ordering."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        media = data.get("media", [])
        if not chat_id or (not content and not media):
            return
        is_group = chat_id.startswith("group:")
        actual_id = chat_id[len("group:"):] if is_group else chat_id
        item: dict[str, Any] = {
            "chat_id": actual_id,
            "sender_id": actual_id if not is_group else "",
            "is_group": is_group,
        }
        if content:
            item["content"] = content
        if media:
            # Append media file paths to content so _detect_and_upload_media handles them
            media_text = ""
            for path in media:
                ext = Path(path).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    media_text += f"\n![image]({path})"
                else:
                    name = os.path.basename(path)
                    media_text += f"\n[DINGTALK_FILE]{{\"path\": \"{path}\", \"name\": \"{name}\"}}[/DINGTALK_FILE]"
            item["content"] = (content or "") + media_text
        self._enqueue_send(item)

    def _get_access_token(self) -> str | None:
        """Get DingTalk access token."""
        try:
            import httpx
            url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
            data = {
                "appKey": self.config.get("clientId", ""),
                "appSecret": self.config.get("clientSecret", ""),
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=data)
                if resp.status_code == 200:
                    return resp.json().get("accessToken")
        except Exception:
            logger.exception("Failed to get DingTalk access token")
        return None

    def start(self) -> None:
        """Run the DingTalk Stream connection in its own event loop."""
        from dingtalk_stream import CallbackHandler, DingTalkStreamClient, Credential
        from dingtalk_stream.chatbot import ChatbotMessage

        credential = Credential(self.config.get("clientId", ""), self.config.get("clientSecret", ""))
        stream_client = DingTalkStreamClient(credential)

        _channel = self  # closure reference for Handler

        class Handler(CallbackHandler):
            async def process(self, message):
                _channel.on_message(message.data)
                return 0, "OK"

        stream_client.register_callback_handler(ChatbotMessage.TOPIC, Handler())

        def run_stream() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            while True:
                try:
                    loop.run_until_complete(stream_client.start())
                except Exception as e:
                    logger.error("DingTalk stream error: {}", e)
                    time.sleep(5)

        thread = threading.Thread(target=run_stream, daemon=True)
        thread.start()

        while True:
            time.sleep(5)


def main() -> None:
    DingTalkProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("DingTalk proxy crashed: {}", traceback.format_exc())
        sys.exit(1)