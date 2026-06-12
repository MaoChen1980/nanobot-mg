"""Email proxy - runs as a separate process, polls IMAP and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import html
import imaplib
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import time
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any

from loguru import logger

from nanobot.proxy.channels.base import BaseProxyChannel


class EmailProxyChannel(BaseProxyChannel):
    """Polls IMAP and forwards messages to Hub via TCP."""

    CHANNEL_NAME = "Email"
    REQUIRED_CONFIG_FIELDS = ["imap_host", "imap_username", "imap_password", "smtp_host", "smtp_username", "smtp_password"]

    def __init__(self, config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._self_addresses: set[str] = self._collect_self_addresses()

    def _collect_self_addresses(self) -> set[str]:
        candidates = (
            self.config.get("from_address", ""),
            self.config.get("smtp_username", ""),
            self.config.get("imap_username", ""),
        )
        normalized = set()
        for candidate in candidates:
            raw = (candidate or "").strip()
            if not raw:
                continue
            addr = parseaddr(raw)[1].strip().lower()
            if addr:
                normalized.add(addr)
            elif "@" in raw:
                normalized.add(raw.lower())
        return normalized

    def _normalize_address(self, value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        parsed = parseaddr(raw)[1].strip().lower()
        if parsed:
            return parsed
        if "@" in raw:
            return raw.lower()
        return ""

    def _is_self_address(self, sender: str) -> bool:
        normalized = self._normalize_address(sender)
        return bool(normalized) and normalized in self._self_addresses

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            logger.warning("Failed to decode email header", exc_info=True)
            return value

    def _extract_text_body(self, msg: Any) -> tuple[str, list[str]]:
        """Extract text body and save attachments.

        Returns:
            Tuple of (body_text, list_of_saved_attachment_paths).
        """
        media_paths: list[str] = []
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    filename = part.get_filename()
                    if filename:
                        data = part.get_payload(decode=True)
                        if data:
                            path = self._save_media_bytes(filename, data)
                            media_paths.append(path)
                    continue
                content_type = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            if plain_parts:
                body = "\n\n".join(plain_parts).strip()
            elif html_parts:
                body = self._html_to_text("\n\n".join(html_parts)).strip()
            else:
                body = ""
        else:
            try:
                payload = msg.get_content()
            except Exception:
                payload_bytes = msg.get_payload(decode=True) or b""
                charset = msg.get_content_charset() or "utf-8"
                payload = payload_bytes.decode(charset, errors="replace")
            if not isinstance(payload, str):
                body = ""
            elif msg.get_content_type() == "text/html":
                body = self._html_to_text(payload).strip()
            else:
                body = payload.strip()

        if media_paths:
            names = ", ".join(os.path.basename(p) for p in media_paths)
            if body:
                body += f"\n[附件: {names}]"
            else:
                body = f"[附件: {names}]"

        return body, media_paths

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _fetch_new_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        try:
            imap_host = self.config.get("imap_host", "")
            imap_port = self.config.get("imap_port", 993)
            imap_username = self.config.get("imap_username", "")
            imap_password = self.config.get("imap_password", "")
            imap_mailbox = self.config.get("imap_mailbox", "INBOX")
            self.config.get("imap_use_ssl", True)
            mark_seen = self.config.get("mark_seen", True)
            max_body_chars = self.config.get("max_body_chars", 12000)

            if self.config.get("imap_use_ssl", True):
                client = imaplib.IMAP4_SSL(imap_host, imap_port)
            else:
                client = imaplib.IMAP4(imap_host, imap_port)

            client.login(imap_username, imap_password)
            status, _ = client.select(imap_mailbox)
            if status != "OK":
                client.logout()
                return messages

            status, data = client.search(None, "UNSEEN")
            if status != "OK" or not data:
                client.logout()
                return messages

            ids = data[0].split()
            for imap_id in ids:
                try:
                    status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                    if status != "OK" or not fetched:
                        continue

                    raw_bytes = None
                    for item in fetched:
                        if isinstance(item, tuple) and len(item) >= 2:
                            raw_bytes = item[1]
                            break

                    if raw_bytes is None:
                        continue

                    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
                    sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                    if not sender:
                        continue

                    if self._is_self_address(sender):
                        if mark_seen:
                            client.store(imap_id, "+FLAGS", "\\Seen")
                        continue

                    # Extract UID
                    uid = ""
                    for item in fetched:
                        if isinstance(item, tuple) and item:
                            head = bytes(item[0]).decode("utf-8", errors="ignore")
                            m = re.search(r"UID\s+(\d+)", head)
                            if m:
                                uid = m.group(1)
                                break

                    message_id = parsed.get("Message-ID", "").strip()
                    if uid and self.check_duplicate(uid):
                        continue

                    subject = self._decode_header_value(parsed.get("Subject", ""))
                    body, media_paths = self._extract_text_body(parsed)
                    if not body:
                        body = "(empty email body)"
                    body = body[:max_body_chars]

                    if subject:
                        self._last_subject_by_chat[sender] = subject
                    if message_id:
                        self._last_message_id_by_chat[sender] = message_id

                    messages.append({
                        "sender": sender,
                        "subject": subject,
                        "message_id": message_id,
                        "uid": uid,
                        "media": media_paths,
                        "content": (
                            f"[EMAIL-CONTEXT] Email received.\n"
                            f"From: {sender}\nSubject: {subject}\n\n{body}"
                        ),
                    })


                    if mark_seen:
                        client.store(imap_id, "+FLAGS", "\\Seen")

                except Exception as e:
                    logger.warning("Email fetch error for id {}: {}", imap_id, e)
                    continue

            client.logout()
        except Exception as e:
            logger.error("Email IMAP polling error: {}", e)
        return messages

    def _smtp_send(self, to_addr: str, content: str, subject: str | None = None, in_reply_to: str | None = None, media_paths: list[str] | None = None) -> None:
        try:
            smtp_host = self.config.get("smtp_host", "")
            smtp_port = self.config.get("smtp_port", 587)
            smtp_username = self.config.get("smtp_username", "")
            smtp_password = self.config.get("smtp_password", "")
            smtp_use_tls = self.config.get("smtp_use_tls", True)
            smtp_use_ssl = self.config.get("smtp_use_ssl", False)
            from_address = self.config.get("from_address") or smtp_username

            email_msg = EmailMessage()
            email_msg["From"] = from_address
            email_msg["To"] = to_addr
            email_msg["Subject"] = subject or f"Re: {self._last_subject_by_chat.get(to_addr, 'nanobot reply')}"
            email_msg.set_content(content)
            if in_reply_to:
                email_msg["In-Reply-To"] = in_reply_to
                email_msg["References"] = in_reply_to

            # Attach media files referenced in content or passed explicitly
            all_media: set[str] = set(media_paths or [])
            for path, _ in self._scan_media_paths(content):
                all_media.add(path)
            for path in all_media:
                try:
                    filename = os.path.basename(path)
                    with open(path, "rb") as f:
                        data = f.read()
                    mime_type, _ = mimetypes.guess_type(filename)
                    if mime_type:
                        maintype, _, subtype = mime_type.partition("/")
                    else:
                        maintype, subtype = "application", "octet-stream"
                    email_msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
                except Exception as e:
                    logger.warning("Failed to attach file {}: {}", path, e)

            timeout = 30
            if smtp_use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout) as smtp:
                    smtp.login(smtp_username, smtp_password)
                    smtp.send_message(email_msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as smtp:
                    if smtp_use_tls:
                        smtp.starttls(context=ssl.create_default_context())
                    smtp.login(smtp_username, smtp_password)
                    smtp.send_message(email_msg)
        except Exception as e:
            logger.error("Email SMTP send error: {}", e)

    def _process_send(self, item: dict) -> None:
        """Send queued email via SMTP."""
        self._smtp_send(
            to_addr=item["to"],
            content=item["content"],
            subject=item.get("subject"),
            in_reply_to=item.get("in_reply_to"),
            media_paths=item.get("media"),
        )

    async def _handle_deliver(self, data: dict[str, Any]) -> None:
        """Enqueue push delivery from hub via email."""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if chat_id and content:
            self._enqueue_send({
                "to": chat_id,
                "content": content,
                "media": data.get("media", []),
            })

    def start(self) -> None:
        """Poll IMAP and forward messages to Hub."""
        poll_interval = max(5, self.config.get("poll_interval_seconds", 30))

        while True:
            try:
                items = self._fetch_new_messages()
                for item in items:
                    sender = item["sender"]
                    subject = item.get("subject", "")
                    message_id = item.get("message_id", "")
                    uid = item.get("uid", "")
                    content = item["content"]

                    msg_id = uid or message_id
                    if not msg_id:
                        msg_id = str(time.time())

                    msg_data = self.build_message(sender, sender, content, msg_id, media=item.get("media", []))
                    self.send_to_hub(msg_data)
            except Exception as e:
                logger.error("Email poll loop error: {}", e)

            time.sleep(poll_interval)


def main() -> None:
    EmailProxyChannel.run_main()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Email proxy crashed: {}", traceback.format_exc())
        sys.exit(1)
