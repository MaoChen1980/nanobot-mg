"""Feishu proxy - runs as a separate process, connects to Feishu WebSocket and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel
from nanobot.proxy.protocol import HubResponse, ProxyMessage


class FeishuProxyChannel(BaseProxyChannel):
    """Feishu message events forwarded to Hub via TCP."""

    CHANNEL_NAME = "Feishu"
    REQUIRED_CONFIG_FIELDS = ["appId", "appSecret"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._client: Any = None  # lark_oapi Client, set in start()
        self._reaction_emoji = config.get("react_emoji", "THUMBSUP")
        self._done_emoji = config.get("done_emoji")
        self._domain = (
            "https://open.feishu.cn"
            if config.get("domain", "feishu") == "feishu"
            else "https://open.larksuite.com"
        )
        self._thread_pool = ThreadPoolExecutor(max_workers=8)
        self._api_lock = threading.Lock()  # serialize Feishu API calls across concurrent paths
        self._notified_chats: set[str] = set()  # chat_ids already sent ready notification
        self._consumed_qids: set[str] = set()  # chat-scoped QIDs already clicked
        self._last_chat_id: str = self._load_last_chat_id()  # last chat that sent a message → used for ready notification

    # ------------------------------------------------------------------
    # State persistence (last chat for startup notification)
    # ------------------------------------------------------------------

    def _state_file(self) -> str:
        """Path to the state file storing last_chat_id."""
        # Use config directory, e.g. ~/.nanobot/config.json → parent dir
        config_path = os.environ.get("NANOBOT_CONFIG_PATH", "")
        if config_path:
            state_dir = os.path.dirname(config_path)
        else:
            state_dir = os.path.expanduser("~/.nanobot")
        return os.path.join(state_dir, "proxy_feishu_last_chat.json")

    def _load_last_chat_id(self) -> str:
        """Load last chat_id from state file."""
        try:
            path = self._state_file()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("last_chat_id", "")
        except Exception as e:
            logger.debug("Failed to load last_chat_id: {}", e)
        return ""

    def _save_last_chat_id(self, chat_id: str) -> None:
        """Persist last chat_id to state file."""
        try:
            path = self._state_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"last_chat_id": chat_id}, f)
        except Exception as e:
            logger.debug("Failed to save last_chat_id: {}", e)

    # ------------------------------------------------------------------
    # Message handler (called from Feishu SDK thread)
    # ------------------------------------------------------------------

    def on_message(self, data: Any) -> None:
        """Sync callback from Feishu SDK - forward message to Hub."""
        logger.info("Feishu WS on_message called: data={}", type(data).__name__)
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = getattr(message, "message_id", None)
            if not message_id or self.check_duplicate(message_id):
                return

            # Skip stale messages (platform sometimes redelivers old messages)
            create_time = getattr(message, "create_time", None)
            if create_time and self._is_stale_message(float(create_time), self._max_message_age):
                return

            # DEBUG: dump raw message structure to diagnose empty content
            msg_attrs = {a: getattr(message, a) for a in ['message_id', 'message_type', 'content', 'chat_id', 'root_id', 'parent_id', 'chat_type']
                         if hasattr(message, a)}
            logger.debug("Feishu message attrs: {}", msg_attrs)
            body = getattr(message, "body", None)
            content = getattr(body, "content", "") if body else getattr(message, "content", "")
            logger.debug("Feishu content extraction: has_body={}, has_content={}, content_len={}, content_preview={!r}",
                         body is not None, hasattr(message, "content"),
                         len(content) if content else 0, (content or "")[:200])
            # WS event: EventMessage uses "message_type"; REST API: Message uses "msg_type"
            msg_type = getattr(message, "msg_type", None) or getattr(message, "message_type", None) or "text"
            file_key = None
            try:
                content_obj = json.loads(content)
                text = content_obj.get("text", "") if isinstance(content_obj, dict) else str(content_obj)
                if msg_type in ("image", "file", "audio", "video") and isinstance(content_obj, dict):
                    file_key = (content_obj.get("file_key") or content_obj.get("image_key")
                                or content_obj.get("key") or content_obj.get("token"))
            except Exception:
                text = content

            sender_id_obj = getattr(sender, "sender_id", None)
            if sender_id_obj is not None and hasattr(sender_id_obj, "open_id"):
                sender_id = sender_id_obj.open_id
            else:
                sender_id = str(sender_id_obj or "")
            chat_id = getattr(message, "chat_id", "")

            # Offload all blocking work (Feishu API calls, Hub TCP) to thread pool
            parent_id = getattr(message, "parent_id", None)

            def _process() -> None:
                try:
                    self._last_chat_id = chat_id  # remember last sender for ready notification
                    self._save_last_chat_id(chat_id)
                    quoted_text = self._fetch_quoted_message(parent_id) if parent_id else ""
                    self._add_reaction(message_id, self._reaction_emoji)

                    msg_data = self.build_message(sender_id, chat_id, text, message_id)
                    if quoted_text:
                        msg_data["metadata"] = {"quoted_message": quoted_text}

                    logger.debug("Feishu sending to hub: text={!r}, file_key={}, msg_type={}",
                                 text[:100] if text else "", file_key, msg_type)

                    # ── Download inbound media (image/file/audio/video) ───────
                    if file_key and msg_type in ("image", "file", "audio", "video"):
                        local_path = self._download_media(file_key, msg_type, message_id)
                        if local_path:
                            msg_data["media"] = [local_path]
                            logger.info("Feishu inbound media: type={}, key={} → {}", msg_type, file_key, local_path)

                    response = self.send_to_hub(msg_data)
                    if response and response.success:
                        item: dict[str, Any] = {"chat_id": chat_id, "root_id": message_id}
                        if response.content:
                            item["content"] = response.content
                        if response.media:
                            item["media"] = response.media
                        logger.info("Feishu enqueue response: {}:{}", chat_id[:20], response.content[:60] if response.content else "(media only)")
                        self._enqueue_send(item)
                    elif response and not response.success and response.error:
                        logger.error("Hub returned error for message {}: {}", message_id, response.error)
                        self._send_plain_text(chat_id, response.error)
                    if response and response.success and response.metadata.get("done_emoji"):
                        self._add_reaction(message_id, response.metadata["done_emoji"])
                    elif response:
                        self._add_reaction(message_id, self._done_emoji)
                    self._remove_reaction(message_id)
                except Exception as e:
                    logger.error("Feishu on_message process error: {}", e)

            self._thread_pool.submit(_process)

        except Exception as e:
            logger.error("Feishu proxy message handler error: {}", e)

    def on_reaction(self, data: Any) -> None:
        """Handle reaction events (im.message.reaction.created_v1)."""
        pass

    def on_bot_enter_chat(self, data: Any) -> None:
        """Suppress 'processor not found' error for bot_p2p_chat_entered_v1."""
        pass

    def on_message_read(self, data: Any) -> None:
        """Suppress 'processor not found' error for message_read_v1."""
        pass

    def on_card_action(self, data: Any) -> None:
        """Handle card button click — forward reply text to Hub as user message."""
        try:
            event = data.event  # P2CardActionTriggerData
            operator = event.operator  # CallBackOperator
            action = event.action  # CallBackAction

            value: dict = action.value if isinstance(action.value, dict) else {}

            reply_text = value.get("qr", "")
            chat_id = value.get("cid", "")
            qid = value.get("qid", reply_text)
            card_token = value.get("token", "")
            if not reply_text or not chat_id:
                logger.warning("Card action missing qr/cid: {}", value)
                return

            # CallBackOperator has .open_id directly (not .operator_id.open_id)
            sender_id = operator.open_id if operator else ""
            if not sender_id:
                logger.warning("Card action missing open_id in operator")
                return

            # Per-card dedup: token makes each card's buttons unique
            dedup_key = f"qr:{chat_id}:{qid}:{card_token}" if card_token else f"qr:{chat_id}:{qid}"
            if dedup_key in self._consumed_qids:
                logger.info("QID already consumed, notifying user: {}", dedup_key)
                self._send_plain_text(chat_id, f'您已经选择了"{reply_text}"')
                return

            # Deduplicate by action name to avoid double-clicks
            if self.check_duplicate(action.name or ""):
                return

            logger.info("Card action: {} -> {} (from {})", reply_text[:50], chat_id, sender_id)
            self._consumed_qids.add(dedup_key)
            # Offload blocking work (Feishu API, Hub TCP) to thread pool
            self._thread_pool.submit(
                lambda: self._process_card_action(chat_id, sender_id, reply_text, action.name or "")
            )
        except Exception as e:
            logger.error("Failed to handle card action: {}", e)

    def _process_card_action(self, chat_id: str, sender_id: str, reply_text: str, action_name: str) -> None:
        """Process card action after dedup — runs on thread pool."""
        try:
            self._send_plain_text(chat_id, f"你选择了'{reply_text}'")
            msg_data = self.build_message(sender_id, chat_id, reply_text, f"card_{action_name}")
            result = self.send_to_hub(msg_data)
            if result and result.success and result.content:
                self._enqueue_send({"chat_id": chat_id, "root_id": None, "content": result.content})
        except Exception as e:
            logger.error("Failed to process card action: {}", e)

    # ------------------------------------------------------------------
    # Fetch quoted message
    # ------------------------------------------------------------------

    def _fetch_quoted_message(self, message_id: str) -> str:
        """Fetch the content of the message being replied to."""
        try:
            from lark_oapi.api.im.v1 import GetMessageRequest
            request = GetMessageRequest.builder().message_id(message_id).build()
            response = self._client.im.v1.message.get(request)
            if response.success():
                items = response.data.items
                if items:
                    content_str = items[0].body.content
                    obj = json.loads(content_str)
                    if isinstance(obj, dict):
                        return obj.get("text", "") or obj.get("content", "") or str(obj)
                    return str(obj)
        except Exception as e:
            logger.debug("Failed to fetch quoted message {}: {}", message_id, e)
        return ""

    # ------------------------------------------------------------------
    # Push delivery from Hub (cron reminders, etc.)
    # ------------------------------------------------------------------

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub to Feishu chat.

        Used for both progress updates (cron / think / tool events) and Bot
        responses.  When *reply_to* is present the message is sent as a
        threaded reply; otherwise it's a standalone message.
        """
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        media = data.get("media", [])
        buttons = data.get("buttons", [])
        reply_to = data.get("reply_to", "")
        if not chat_id or (not content and not media):
            logger.warning("Feishu _handle_deliver: dropped (no chat_id or empty content+media)")
            return
        # Convert structured buttons to ---quick-replies format for _send_formatted_reply
        if buttons and content:
            qr_lines = []
            for row in buttons:
                for btn in row:
                    qr_lines.append(str(btn))
            if qr_lines:
                content = content.rstrip() + "\n\n---quick-replies\n" + "\n".join(qr_lines)
        item: dict[str, Any] = {"chat_id": chat_id, "root_id": reply_to or None}
        if content:
            item["content"] = content
        if media:
            item["media"] = media
        self._enqueue_send(item)
        logger.info("Enqueued deliver to {}: content={} media={} buttons={} reply_to={}", chat_id, content[:60] if content else "", len(media), len(buttons), reply_to[:20] if reply_to else "")

    def _process_send(self, item: dict) -> None:
        """Send queued message to Feishu."""
        content = item.get("content", "")
        # Send each media item individually with its own msg_type.
        # Grouping by type would be more efficient but the Feishu upload API
        # is strict about file format (rejects a .md file when msg_type is image).
        media_list = item.get("media")
        if media_list:
            logger.info("Feishu _process_send: sending {} media items to chat {}",
                        len(media_list), item["chat_id"])
            for path in media_list:
                is_image = path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
                self._send_media(item["chat_id"], item.get("root_id"), [path],
                                 msg_type="image" if is_image else "file")
        if content:
            self._send_formatted_reply(
                chat_id=item["chat_id"],
                root_id=item.get("root_id"),
                content=content,
            )

    # ------------------------------------------------------------------
    # Media download (inbound: Feishu → local file)
    # ------------------------------------------------------------------

    def _download_media(self, file_key: str, msg_type: str, message_id: str) -> str | None:
        """Download a media resource from Feishu and return the local file path.

        Uses ``GetMessageResource`` API (``/im/v1/messages/:message_id/resources/:file_key``)
        because ``GetImage`` / ``GetFile`` only work for resources the bot itself uploaded.
        User-sent images/files require the message-scoped resource API.

        Args:
            file_key: Feishu file key / image_key for the resource.
            msg_type: Feishu message type (image, file, audio, video).
            message_id: The Feishu message_id containing the resource.

        Returns:
            Local file path on success, ``None`` on failure.
        """
        try:
            import io, pathlib, tempfile, time
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type("image" if msg_type == "image" else "file")
                .build()
            )
            resp = self._client.im.v1.message_resource.get(request)

            if not resp.success():
                logger.warning("Feishu media download failed: code={} msg={}", resp.code, resp.msg)
                return None

            file_obj = resp.file
            if file_obj is None:
                logger.warning("Feishu media download got empty response for key={}", file_key)
                return None

            data_bytes = file_obj.read() if isinstance(file_obj, io.IOBase) else file_obj
            if isinstance(data_bytes, memoryview):
                data_bytes = bytes(data_bytes)
            if not data_bytes:
                logger.warning("Feishu media download got empty data for key={}", file_key)
                return None

            ext = self._guess_ext_from_resp(resp, msg_type, file_key)
            ws = self.config.get("_workspace_path") or str(pathlib.Path.home() / ".nanobot" / "workspace")
            tmp_dir = pathlib.Path(ws) / "incoming"
            tmp_dir.mkdir(parents=True, exist_ok=True)

            original_name = getattr(resp, "file_name", None) or ""
            if original_name:
                local_path = tmp_dir / original_name
                # deduplicate: append counter if filename already exists
                if local_path.exists():
                    stem = local_path.stem
                    suffix = local_path.suffix
                    counter = 1
                    while local_path.exists():
                        local_path = tmp_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
            else:
                local_path = tmp_dir / f"{int(time.time() * 1000)}_{file_key[:16]}{ext}"

            with open(local_path, "wb") as f:
                f.write(data_bytes)
            logger.info("Feishu media downloaded: {} bytes → {}", len(data_bytes), local_path)
            return str(local_path)
        except Exception as e:
            logger.error("Feishu media download failed for key={}: {}", file_key, e)
            return None

    def _guess_ext_from_resp(self, resp, msg_type: str, file_key: str) -> str:
        """Extract file extension from lark response file_name or msg_type."""
        file_name = getattr(resp, "file_name", None) or ""
        if "." in file_name:
            return "." + file_name.rsplit(".", 1)[-1]
        ext_map = {"image": ".jpg", "audio": ".m4a", "video": ".mp4", "file": ".bin"}
        return ext_map.get(msg_type, ".bin")

    # ------------------------------------------------------------------
    # Media upload (outbound: local file → Feishu)
    # ------------------------------------------------------------------

    def _upload_media_to_feishu(self, local_path: str, msg_type: str) -> str | None:
        """Upload a local file to Feishu using the direct HTTP API.

        We use httpx directly rather than ``lark_oapi`` because the SDK's sync
        ``image.create()`` / ``file.create()`` do not handle multipart file
        uploads — only the async ``acreate()`` variants do, and the send worker
        thread has no event loop.

        Returns:
            Feishu token (``image_key`` for images, ``file_key`` for files) on success,
            ``None`` on failure.
        """
        try:
            import httpx

            token = self._get_tenant_access_token()
            if not token:
                return None

            with open(local_path, "rb") as f:
                file_data = f.read()

            headers = {"Authorization": f"Bearer {token}"}
            is_image = msg_type == "image"

            if is_image:
                url = f"{self._domain}/open-apis/im/v1/images"
                data = {"image_type": "message"}
            else:
                url = f"{self._domain}/open-apis/im/v1/files"
                data = {
                    "file_type": self._feishu_file_type(local_path),
                    "file_name": os.path.basename(local_path),
                }

            # Use ``files`` for the binary and ``data`` for the form fields.
            # The field name differs: "image" for images, "file" for files.
            field = "image" if is_image else "file"
            files = {field: (os.path.basename(local_path), file_data)}

            with httpx.Client(timeout=60) as client:
                resp = client.post(url, data=data, files=files, headers=headers)

            if resp.status_code != 200:
                logger.error("Feishu media upload HTTP {}: {}", resp.status_code, resp.text[:300])
                return None

            body = resp.json()
            if body.get("code") != 0:
                logger.error("Feishu media upload failed: code={} msg={}", body.get("code"), body.get("msg"))
                return None

            data_obj = body.get("data", {}) or {}
            key = data_obj.get("image_key", "") or data_obj.get("file_key", "")
            if key:
                logger.info("Feishu media uploaded: key={}", key)
                return key

            logger.error("Feishu media upload: no key in response: {}", body)
            return None
        except Exception as e:
            logger.error("Feishu media upload error: {}", e)
            return None

    def _get_tenant_access_token(self) -> str | None:
        """Get a Feishu tenant access token via the internal app auth endpoint."""
        try:
            import httpx
            url = f"{self._domain}/open-apis/auth/v3/tenant_access_token/internal"
            payload = {
                "app_id": self.config.get("appId", ""),
                "app_secret": self.config.get("appSecret", ""),
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return resp.json().get("tenant_access_token")
                logger.error("Feishu tenant token failed: {} - {}", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Feishu tenant token error: {}", e)
        return None

    @staticmethod
    def _feishu_file_type(file_path: str) -> str:
        """Map file extension to Feishu ``file_type`` parameter.

        Feishu's ``/open-apis/im/v1/files`` endpoint requires a concrete
        *file_type* — ``"stream"`` is the catch-all for unknown types.
        """
        ext = os.path.splitext(file_path)[1].lower()
        mapping = {
            ".pdf": "pdf",
            ".doc": "doc",
            ".docx": "docx",
            ".xls": "xls",
            ".xlsx": "xlsx",
            ".ppt": "ppt",
            ".pptx": "pptx",
            ".mp4": "mp4",
        }
        return mapping.get(ext, "stream")

    def _send_media(self, chat_id: str, root_id: str | None, media_paths: list[str], msg_type: str = "file") -> None:
        """Send one or more media files to a Feishu chat.

        Args:
            chat_id: Feishu chat ID.
            root_id: Message ID to reply to (thread root). Pass ``None`` for a standalone message.
            media_paths: List of local file paths.
            msg_type: Feishu message type — ``image`` or ``file``.
        """
        for path in media_paths:
            path = path.strip()
            if not os.path.exists(path):
                logger.warning("Feishu media send: file not found: {}", path)
                continue
            file_key = self._upload_media_to_feishu(path, msg_type)
            if not file_key:
                logger.error("Feishu media send failed: could not upload {}", path)
                continue

            try:
                from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

                receive_id_type = self.config.get("receiveIdType", "chat_id")
                key_field = "image_key" if msg_type == "image" else "file_key"
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type(msg_type)
                        .content(json.dumps({key_field: file_key}))
                        .build()
                    )
                    .build()
                )
                if root_id:
                    request.builder().root_id(root_id)
                resp = self._client.im.v1.message.create(request)
                if resp.code != 0:
                    logger.error("Feishu send media failed: code={} msg={}", resp.code, resp.msg)
                else:
                    logger.info("Feishu media sent: {} → chat={}", os.path.basename(path), chat_id)
            except Exception as e:
                logger.error("Feishu media send error: {}", e)

    # ------------------------------------------------------------------
    # Reply / reaction helpers
    # ------------------------------------------------------------------

    # ── Content detection ──────────────────────────────────────────────

    @staticmethod
    def _has_rich_content(text: str) -> bool:
        """Detect content that benefits from interactive card rendering.

        Checks for code blocks (```` ``` ````) and markdown tables (``|...|``
        followed by a separator line ``|---|``), which Feishu post messages
        and legacy ``lark_md`` tags cannot render properly.
        """
        if "```" in text:
            return True
        return bool(re.search(r'\|.+\|\r?\n\|[-:| ]+\|', text))

    @staticmethod
    def _extract_header(content: str) -> tuple[str | None, str]:
        """Extract first level-1 heading as a card header title.

        Looks for ``# Title`` among the first few non-empty lines. When found,
        the heading line is removed from the body content so it doesn't
        render twice — once in the header bar and once in the body.

        Returns ``(header_title, remaining_content)``.
        """
        lines = content.split("\n")
        for i, line in enumerate(lines[:10]):
            stripped = line.strip()
            if stripped:
                m = re.match(r"^#\s+(.+)$", stripped)
                if m:
                    body = "\n".join(lines[i + 1 :]).strip()
                    return m.group(1), body
                break
        return None, content

    @staticmethod
    def _parse_quick_replies(content: str) -> tuple[str, list[dict[str, str]] | None]:
        """Extract ``---quick-replies`` section from agent response.

        Format::

            ---quick-replies
            标签1                     # label = reply, WYSIWYG

        Each line becomes a button.  When ``||`` is present, label and reply
        are compared and the longer one wins — both label and reply are set
        to that longer text for clarity.  Without ``||``, the whole line is
        used as both.

        Returns ``(cleaned_text, quick_replies_or_None)``.
        """
        marker = "---quick-replies"
        if marker not in content:
            return content, None

        before, section = content.split(marker, 1)
        cleaned = before.strip()

        quick_replies: list[dict[str, str]] = []
        for line in section.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "||" in line:
                parts = [p.strip() for p in line.split("||")]
                if len(parts) == 2:
                    # label || reply: use the longer text for both
                    text = parts[0] if len(parts[0]) >= len(parts[1]) else parts[1]
                    quick_replies.append({"label": text, "reply": text})
                else:
                    # N > 2: each part is its own button (LLM tends to
                    # put all options on one line with || separators)
                    for part in parts:
                        quick_replies.append({"label": part, "reply": part})
            else:
                quick_replies.append({"label": line, "reply": line})

        return cleaned, quick_replies or None

    # ── Table fallback for non-card paths ──────────────────────────────

    @staticmethod
    def _wrap_tables_in_code_fences(content: str) -> str:
        """Wrap markdown tables in code fences for compatibility with non-card message types.

        ``tag: "md"`` in post messages and ``lark_md`` in v1 cards cannot render
        pipe-delimited tables, so wrapping them in ``` fences preserves layout.
        """
        lines = content.split("\n")
        result: list[str] = []
        table_lines: list[str] = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            is_table = stripped.startswith("|") and stripped.endswith("|")

            if is_table:
                if not in_table:
                    in_table = True
                    table_lines = [line]
                else:
                    table_lines.append(line)
            else:
                if in_table:
                    if len(table_lines) > 2:
                        result.append("```")
                        result.extend(table_lines)
                        result.append("```")
                    else:
                        result.extend(table_lines)
                    in_table = False
                    table_lines = []
                result.append(line)

        if in_table:
            if len(table_lines) > 2:
                result.append("```")
                result.extend(table_lines)
                result.append("```")
            else:
                result.extend(table_lines)

        return "\n".join(result)

    # ── Send strategies ────────────────────────────────────────────────

    def _send_card_reply(self, chat_id: str, content: str,
                          quick_replies: list[dict[str, str]] | None = None) -> bool:
        """Send as Feishu interactive card v2.0 with native markdown.

        Supports the full markdown spec including tables, code blocks,
        headings, lists, and inline formatting. Caller should fall back to
        :meth:`_send_post_reply` or :meth:`_send_plain_text` on failure.

        When *quick_replies* is provided, buttons are appended to the card.
        """
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            header_text, body = self._extract_header(content)
            elements: list[dict[str, Any]] = [
                {"tag": "markdown", "content": body or content},
            ]

            if quick_replies:
                card_token = str(time.time_ns())
                for qr in quick_replies:
                    elements.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": qr["label"]},
                        "type": "default",
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "qr": qr["reply"],
                                    "qid": qr["reply"],
                                    "cid": chat_id,
                                    "token": card_token,
                                },
                            }
                        ],
                    })

            card: dict[str, Any] = {
                "schema": "2.0",
                "config": {"width_mode": "fill"},
                "body": {"elements": elements},
            }
            if header_text:
                template = self.config.get("cardTemplate", "blue")
                card["header"] = {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": template,
                }
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(json.dumps(card))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if resp.success():
                logger.info("Feishu card sent OK to chat={} content_len={}", chat_id, len(content))
                return True
            logger.warning("Feishu card send failed ({}): {} - will fall back", resp.code, resp.msg)
        except Exception as e:
            logger.error("Feishu card send exception: {}", e)
        return False

    def _send_post_reply(self, chat_id: str, content: str) -> bool:
        """Send as post message with a markdown body.

        Lighter than interactive cards — good for simple text without
        tables or code blocks. ``tag: "md"`` supports bold, italic,
        inline code, links, and lists.
        """
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            payload = {
                "zh_cn": {
                    "content": [
                        [{"tag": "md", "text": content}],
                    ],
                },
            }
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("post")
                    .content(json.dumps(payload))
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(request)
            if resp.success():
                logger.info("Feishu post sent OK to chat={} content_len={}", chat_id, len(content))
                return True
            logger.warning("Feishu post send failed ({}): {} - will fall back", resp.code, resp.msg)
        except Exception as e:
            logger.error("Post send exception: {}", e)
        return False

    def _send_plain_text(self, chat_id: str, content: str) -> None:
        """Last-resort fallback: send as plain text with no formatting."""
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
            if resp.success():
                logger.info("Feishu plain text sent OK to chat={} content_len={}", chat_id, len(content))
            else:
                logger.error("Feishu plain text send failed ({}): {}", resp.code, resp.msg)
        except Exception as e:
            logger.error("Feishu plain-text fallback exception: {}", e)

    # ── Public send ────────────────────────────────────────────────────

    def _send_formatted_reply(self, chat_id: str, root_id: str | None, content: str) -> None:
        """Send a reply with automatic format selection based on content and config.

        Routing logic (config key ``renderMode``):
          * ``card`` (default) — always use interactive card v2.0 (native markdown with tables)
          * ``raw`` — use post message (lightweight, tables → code fences)
          * ``auto`` — detect rich content (code blocks, tables) → card; else post

        When the content contains a ``---quick-replies`` section, card mode is
        forced and buttons are rendered from the parsed labels.

        Falls back through the chain: card → post → plain text.

        Note: ``_api_lock`` is only held for the strategy decision (pure Python,
        no I/O), NOT during the HTTP calls. This prevents a single Feishu API
        timeout from stalling the entire send queue.
        """
        # Strategy decision — fast, no I/O
        with self._api_lock:
            cleaned, qrs = self._parse_quick_replies(content)
            render_mode = self.config.get("renderMode", "card")
            use_card = qrs is not None or render_mode == "card" or (
                render_mode == "auto" and self._has_rich_content(cleaned)
            )

        # Execute chosen strategy — HTTP calls happen outside the lock
        if use_card:
            if self._send_card_reply(chat_id, cleaned, quick_replies=qrs):
                return

        processed = self._wrap_tables_in_code_fences(cleaned)
        if self._send_post_reply(chat_id, processed):
            return

        self._send_plain_text(chat_id, processed)

    def _add_reaction(self, message_id: str, emoji: str) -> None:
        """Add reaction emoji to message."""
        if not emoji:
            return
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                Emoji,
            )
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
        except Exception as e:
            logger.debug("Failed to remove reaction: {}", e)

    # ------------------------------------------------------------------
    # Lifecycle: startup notification (override base._send_startup_notification)
    # ------------------------------------------------------------------

    async def _send_startup_notification(self) -> None:
        """Send startup notification to the last chat that messaged us."""
        if not self._last_chat_id or self._last_chat_id in self._notified_chats:
            return
        # Wait a moment for WS to settle
        await asyncio.sleep(2)
        # Double-check after sleep in case another task beat us
        if self._last_chat_id in self._notified_chats:
            return
        self._notified_chats.add(self._last_chat_id)
        try:
            # Use to_thread to avoid blocking conn_loop
            await asyncio.to_thread(
                self._send_plain_text, self._last_chat_id, "Nano Bot 已启动，Proxy ready ✅"
            )
            logger.info("Startup notification sent to {}", self._last_chat_id)
        except Exception as e:
            logger.error("Failed to send startup notification: {}", e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Set up Feishu WebSocket client and enter the event loop."""
        import lark_oapi as lark

        self._client = (
            lark.Client.builder()
            .app_id(self.config["appId"])
            .app_secret(self.config["appSecret"])
            .domain(self._domain)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        builder = (
            lark.EventDispatcherHandler.builder(
                self.config.get("encryptKey", "") or "",
                self.config.get("verificationToken", "") or "",
            )
            .register_p2_im_message_receive_v1(self.on_message)
            .register_p2_im_message_reaction_created_v1(self.on_reaction)
            .register_p2_card_action_trigger(self.on_card_action)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self.on_bot_enter_chat)
            .register_p2_im_message_message_read_v1(self.on_message_read)
        )
        event_handler = builder.build()

        ws_client = lark.ws.Client(
            self.config["appId"],
            self.config["appSecret"],
            domain=self._domain,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def run_ws() -> None:
            import lark_oapi.ws as _lark_ws

            logger.info("Feishu WS loop starting, connecting to {}...", self._domain)
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws.client.loop = ws_loop
            try:
                ws_client.start()
                logger.info("Feishu WS: client.start() returned (should not happen)")
            except Exception as e:
                logger.error("Feishu WS error: {}", e)
            finally:
                ws_loop.close()
                logger.info("Feishu WS loop ended")

        thread = threading.Thread(target=run_ws, daemon=True)
        thread.start()

        # Wait for WS loop to initialize (lark client is created in this method)
        # then send startup notification to last chat that messaged us.
        # Only send if we have a last_chat_id to avoid empty notifications.
        if self._last_chat_id:
            asyncio.run_coroutine_threadsafe(self._send_startup_notification(), self._conn_loop)

        while True:
            time.sleep(5)


def main() -> None:
    FeishuProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Feishu proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
