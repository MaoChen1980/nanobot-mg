"""WhatsApp proxy — runs as a separate process, uses neonize instead of Node.js bridge."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel

# ---------------------------------------------------------------------------
# Lazy neonize loader
# ---------------------------------------------------------------------------

class _NeonizeAPI(NamedTuple):
    NewAClient: Any
    ConnectedEv: Any
    DisconnectedEv: Any
    MessageEv: Any
    PairStatusEv: Any
    build_jid: Any


_NEONIZE_API: _NeonizeAPI | None = None
_JID_RE = re.compile(r"^(?P<user>[^@]+)@(?P<server>[^@]+)$")


def _load_neonize() -> _NeonizeAPI:
    global _NEONIZE_API
    if _NEONIZE_API is not None:
        return _NEONIZE_API
    try:
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, DisconnectedEv, MessageEv, PairStatusEv
        from neonize.utils.jid import build_jid
    except ImportError as exc:
        raise RuntimeError(
            "neonize not installed. Run: pip install neonize"
        ) from exc
    _NEONIZE_API = _NeonizeAPI(
        NewAClient=NewAClient, ConnectedEv=ConnectedEv,
        DisconnectedEv=DisconnectedEv, MessageEv=MessageEv,
        PairStatusEv=PairStatusEv, build_jid=build_jid,
    )
    return _NEONIZE_API


# ---------------------------------------------------------------------------
# Protobuf field helpers
# ---------------------------------------------------------------------------

def _has_field(message: Any, name: str) -> bool:
    if message is None:
        return False
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            return bool(has_field(name))
        except ValueError:
            pass
    list_fields = getattr(message, "ListFields", None)
    if callable(list_fields):
        try:
            return any(getattr(field, "name", "") == name for field, _ in list_fields())
        except Exception:
            pass
    value = getattr(message, name, None)
    return value is not None and value != "" and value != b""


def _message_field(message: Any, *names: str) -> Any:
    for name in names:
        if _has_field(message, name):
            return getattr(message, name)
    return None


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------
# JID helpers
# ---------------------------------------------------------------------------

def _jid_to_string(jid: Any) -> str:
    if jid is None:
        return ""
    if isinstance(jid, str):
        return jid.strip()
    if bool(_safe_attr(jid, "IsEmpty", False)):
        return ""
    user = str(_safe_attr(jid, "User", "") or "").strip()
    server = str(_safe_attr(jid, "Server", "") or "").strip()
    if user and server:
        return f"{user}@{server}"
    return server or user


def _normalize_jid(raw: Any) -> str:
    jid = _jid_to_string(raw).strip()
    if not jid:
        return ""
    if jid.endswith("@lid.whatsapp.net"):
        return jid[: -len(".whatsapp.net")]
    return jid


def _bare_jid(raw: Any) -> str:
    jid = _normalize_jid(raw)
    if "@" not in jid:
        return jid
    return jid.split("@", 1)[0].split(":", 1)[0]


def _classify_sender_ids(jids: list[Any]) -> tuple[str, str]:
    phone_id = ""
    lid_id = ""
    for raw in jids:
        jid = _normalize_jid(raw)
        if not jid:
            continue
        match = _JID_RE.match(jid)
        if match:
            user = match.group("user").split(":", 1)[0]
            server = match.group("server")
            if server in {"s.whatsapp.net", "c.us"}:
                phone_id = phone_id or user
            elif server in {"lid", "lid.whatsapp.net"}:
                lid_id = lid_id or user
            continue
        if not phone_id:
            phone_id = jid
    return phone_id, lid_id


# ---------------------------------------------------------------------------
# Message content helpers
# ---------------------------------------------------------------------------

def _message_text(message: Any) -> str:
    conversation = str(_safe_attr(message, "conversation", "") or "").strip()
    if conversation:
        return conversation
    extended = _message_field(message, "extendedTextMessage")
    text = str(_safe_attr(extended, "text", "") or "").strip()
    if text:
        return text
    for field_name in ("imageMessage", "videoMessage", "documentMessage", "stickerMessage"):
        media_message = _message_field(message, field_name)
        caption = str(_safe_attr(media_message, "caption", "") or "").strip()
        if caption:
            return caption
    return ""


class _MediaInfo(NamedTuple):
    kind: str
    message: Any
    mimetype: str
    filename: str
    is_voice: bool = False


def _media_message(message: Any) -> _MediaInfo | None:
    image = _message_field(message, "imageMessage")
    if image is not None:
        return _MediaInfo(
            kind="image", message=image,
            mimetype=str(_safe_attr(image, "mimetype", "") or "image/jpeg"),
            filename=str(_safe_attr(image, "fileName", "") or ""),
        )
    video = _message_field(message, "videoMessage")
    if video is not None:
        return _MediaInfo(
            kind="video", message=video,
            mimetype=str(_safe_attr(video, "mimetype", "") or "video/mp4"),
            filename=str(_safe_attr(video, "fileName", "") or ""),
        )
    audio = _message_field(message, "audioMessage")
    if audio is not None:
        return _MediaInfo(
            kind="audio", message=audio,
            mimetype=str(_safe_attr(audio, "mimetype", "") or "audio/ogg"),
            filename=str(_safe_attr(audio, "fileName", "") or ""),
            is_voice=bool(_safe_attr(audio, "PTT", False) or _safe_attr(audio, "ptt", False)),
        )
    document = _message_field(message, "documentMessage")
    if document is not None:
        return _MediaInfo(
            kind="file", message=document,
            mimetype=str(_safe_attr(document, "mimetype", "") or "application/octet-stream"),
            filename=str(_safe_attr(document, "fileName", "") or _safe_attr(document, "title", "") or ""),
        )
    sticker = _message_field(message, "stickerMessage")
    if sticker is not None:
        return _MediaInfo(
            kind="sticker", message=sticker,
            mimetype=str(_safe_attr(sticker, "mimetype", "") or "image/webp"),
            filename=str(_safe_attr(sticker, "fileName", "") or ""),
        )
    return None


# ---------------------------------------------------------------------------
# Proxy channel
# ---------------------------------------------------------------------------

class WhatsAppProxyChannel(BaseProxyChannel):
    """WhatsApp proxy channel using neonize."""

    CHANNEL_NAME = "WhatsApp"
    REQUIRED_CONFIG_FIELDS = []

    def __init__(
        self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    ):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._client: Any = None
        self._connected = False
        self._neonize_loop: asyncio.AbstractEventLoop | None = None
        self._started_at = 0.0

    # ------------------------------------------------------------------
    # Database path
    # ------------------------------------------------------------------

    @staticmethod
    def _database_path() -> Path:
        configured = os.environ.get("NANOBOT_WHATSAPP_DB", "")
        if configured:
            return Path(configured).expanduser()
        return Path.home() / ".nanobot" / "whatsapp-auth" / "neonize.db"

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    def _new_client(self, db_path: str) -> Any:
        return _load_neonize().NewAClient(db_path)

    # ------------------------------------------------------------------
    # JID helpers
    # ------------------------------------------------------------------

    def _build_jid(self, raw: str) -> Any:
        api = _load_neonize()
        target = raw.strip()
        match = _JID_RE.match(_normalize_jid(target))
        if not match:
            return api.build_jid(target)
        user = match.group("user").split(":", 1)[0]
        server = match.group("server")
        return api.build_jid(user, server)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Run WhatsApp client with neonize on its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._neonize_loop = loop

        db_path = self._database_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        client = self._new_client(str(db_path))
        self._client = client
        self._register_handlers(client)

        async def _run():
            self._started_at = time.time()
            try:
                await client.connect()
                self._connected = True
                logger.info("WhatsApp connected via neonize")
                self.notify_ready()
                await client.idle()
            except asyncio.CancelledError:
                raise
            finally:
                self._connected = False

        try:
            loop.run_until_complete(_run())
        except Exception as e:
            logger.error("WhatsApp neonize error: {}", e)
        finally:
            self._client = None
            self._neonize_loop = None
            loop.close()

    # ------------------------------------------------------------------
    # Outbound: Hub → WhatsApp
    # ------------------------------------------------------------------

    async def _handle_deliver(self, data: dict) -> None:
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        media = data.get("media", [])
        if chat_id and (content or media):
            self._enqueue_send({"chat_id": chat_id, "content": content, "media": media})

    def _process_send(self, item: dict) -> None:
        client = self._client
        loop = self._neonize_loop
        if client is None or loop is None or not self._connected:
            return

        try:
            to = self._build_jid(item["chat_id"])
            content = item.get("content", "")
            media_list = list(item.get("media", []))

            # Also scan content for embedded media references (legacy FILE/![] format)
            for path, _mtype in self._scan_media_paths(content):
                if path not in media_list:
                    media_list.append(path)

            # Strip media reference tags for clean caption text
            clean_text = re.sub(
                r"\[FILE\].*?\[/FILE\]|!\[.*?\]\([^)]+\)|file:///[^\s\)\]\}]+",
                "", content,
            ).strip()

            if not media_list:
                # Text-only
                if clean_text:
                    future = asyncio.run_coroutine_threadsafe(
                        client.send_message(to, clean_text), loop,
                    )
                    future.result(timeout=30)
            else:
                # Media with optional text caption on first item
                for i, path in enumerate(media_list):
                    caption = clean_text if i == 0 else None
                    future = asyncio.run_coroutine_threadsafe(
                        self._send_media_async(client, to, path, caption), loop,
                    )
                    future.result(timeout=60)
        except Exception as e:
            logger.error("WhatsApp send error: {}", e)

    async def _send_media_async(self, client: Any, to: Any, path: str, caption: str | None = None) -> None:
        path = str(Path(path).expanduser())
        mime, _ = mimetypes.guess_type(path)
        mimetype = mime or "application/octet-stream"
        if mimetype.startswith("image/"):
            kwargs = {"caption": caption} if caption else {}
            await client.send_image(to, path, **kwargs)
        elif mimetype.startswith("video/"):
            kwargs = {"caption": caption} if caption else {}
            await client.send_video(to, path, **kwargs)
        elif mimetype.startswith("audio/"):
            await client.send_audio(to, path)
        else:
            kwargs = {"caption": caption} if caption else {}
            await client.send_document(to, path, filename=Path(path).name, mimetype=mimetype, **kwargs)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _register_handlers(self, client: Any) -> None:
        api = _load_neonize()

        @client.qr
        async def _on_qr(_: Any, qr_data: bytes) -> None:
            logger.info("Scan the WhatsApp QR code with Linked Devices")
            try:
                import segno
                segno.make_qr(qr_data).terminal(compact=True)
            except ImportError:
                logger.info("QR data (install segno for terminal display): {}", qr_data[:80])

        @client.event(api.ConnectedEv)
        async def _on_connected(_current_client: Any, _: Any) -> None:
            self._connected = True
            logger.info("WhatsApp connected")

        @client.event(api.DisconnectedEv)
        async def _on_disconnected(_: Any, event: Any) -> None:
            self._connected = False
            logger.warning("WhatsApp disconnected: {}", event)

        @client.event(api.PairStatusEv)
        async def _on_pair_status(_: Any, event: Any) -> None:
            error = str(_safe_attr(event, "Error", "") or "")
            if error:
                logger.error("WhatsApp pair status error: {}", error)
            else:
                logger.info("WhatsApp pair status: {}", event)

        @client.event(api.MessageEv)
        async def _on_message(current_client: Any, event: Any) -> None:
            try:
                await self._handle_neonize_message(current_client, event)
            except Exception:
                logger.exception("Error handling WhatsApp message")

    # ------------------------------------------------------------------
    # Inbound: WhatsApp → Hub
    # ------------------------------------------------------------------

    async def _handle_neonize_message(self, client: Any, event: Any) -> None:
        info = _safe_attr(event, "Info")
        message = _safe_attr(event, "Message")
        source = _safe_attr(info, "MessageSource")
        if info is None or message is None or source is None:
            raise ValueError("WhatsApp MessageEv missing Info, Message, or MessageSource")

        if bool(_safe_attr(source, "IsFromMe", False)):
            return

        chat_jid = _normalize_jid(_safe_attr(source, "Chat"))
        if not chat_jid:
            raise ValueError("WhatsApp message has no chat JID")
        if chat_jid == "status@broadcast":
            return

        timestamp = float(_safe_attr(info, "Timestamp", 0) or 0)
        if self._started_at and timestamp and timestamp < self._started_at:
            return

        message_id = str(_safe_attr(info, "ID", "") or "")
        if self.check_duplicate(message_id):
            return

        participant_jid = _normalize_jid(_safe_attr(source, "Sender"))
        sender_alt_jid = _normalize_jid(_safe_attr(source, "SenderAlt"))
        sender_candidates = [sender_alt_jid, participant_jid]
        is_group = bool(_safe_attr(source, "IsGroup", False))
        if not is_group:
            sender_candidates.append(chat_jid)

        phone_id, lid_id = _classify_sender_ids(sender_candidates)
        sender_id = phone_id or lid_id
        if not sender_id:
            raise ValueError("WhatsApp message has no resolvable sender ID")

        text = _message_text(message)
        media_paths: list[str] = []
        media = _media_message(message)
        if media is not None:
            try:
                path = await self._download_media(client, event, media)
                media_paths.append(path)
                label = media.kind if media.kind in {"image", "video", "audio", "sticker"} else "file"
                tag = f"[{label}: {path}]"
                text = f"{text}\n{tag}" if text else tag
            except Exception:
                logger.exception("Failed to download WhatsApp media, forwarding text only")

        if not text and not media_paths:
            return

        msg_data = self.build_message(
            sender_id=sender_id,
            chat_id=chat_jid,
            content=text,
            message_id=message_id,
            media=media_paths,
        )
        self.send_to_hub(msg_data)

    async def _download_media(self, client: Any, event: Any, media: _MediaInfo) -> str:
        info = _safe_attr(event, "Info")
        message_id = str(_safe_attr(info, "ID", "") or "")
        media_dir = Path(self._workspace_dir()) / "incoming"
        media_dir.mkdir(parents=True, exist_ok=True)

        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", message_id or str(int(time.time())))
        filename = Path(media.filename).name if media.filename else ""
        suffix = Path(filename).suffix if filename else ""
        if not suffix:
            suffix = mimetypes.guess_extension(media.mimetype) or {
                "image": ".jpg", "video": ".mp4", "audio": ".ogg", "sticker": ".webp",
            }.get(media.kind, ".bin")
        dest = media_dir / f"wa_{safe_id}_{secrets.token_hex(4)}{suffix}"
        await client.download_any(_safe_attr(event, "Message"), str(dest))
        return str(dest)


def main() -> None:
    WhatsAppProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("WhatsApp proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
