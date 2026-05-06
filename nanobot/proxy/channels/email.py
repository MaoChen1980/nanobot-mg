"""Email proxy - runs as a separate process, polls IMAP and forwards messages to nanobot Hub via TCP."""

from __future__ import annotations

import argparse
import asyncio
import email
import html
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import threading
import time
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any

from loguru import logger

from nanobot.proxy.protocol import HubResponse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Email proxy - polls IMAP and forwards messages to Hub via TCP")
    parser.add_argument("--hub-url", required=True, help="Hub API base URL (ignored, TCP is used)")
    parser.add_argument("--hub-tcp-port", required=True, type=int, help="Hub TCP port for proxy connections")
    parser.add_argument("--channel", required=True, help="Channel name")
    parser.add_argument("--bot", required=True, help="Bot name")
    return parser.parse_args()


def _get_config() -> dict[str, Any]:
    config_str = os.environ.get("NANOBOT_PROXY_CONFIG", "{}")
    return json.loads(config_str)


class EmailProxyChannel:
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
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
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
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

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
            imap_use_ssl = self.config.get("imap_use_ssl", True)
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
                    if uid and uid in self._processed:
                        continue

                    subject = self._decode_header_value(parsed.get("Subject", ""))
                    body = self._extract_text_body(parsed) or "(empty email body)"
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
                        "content": (
                            f"[EMAIL-CONTEXT] Email received.\n"
                            f"From: {sender}\nSubject: {subject}\n\n{body}"
                        ),
                    })

                    if uid:
                        self._processed.add(uid)
                        if len(self._processed) > 10000:
                            self._processed = set(list(self._processed)[-5000:])

                    if mark_seen:
                        client.store(imap_id, "+FLAGS", "\\Seen")

                except Exception as e:
                    logger.warning("Email fetch error for id {}: {}", imap_id, e)
                    continue

            client.logout()
        except Exception as e:
            logger.error("Email IMAP polling error: {}", e)
        return messages

    def _smtp_send(self, to_addr: str, content: str, subject: str | None = None, in_reply_to: str | None = None) -> None:
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


def run_email_loop(
    config: dict, hub_tcp_host: str, hub_tcp_port: int, channel: str, bot: str,
    proxy_channel: EmailProxyChannel,
) -> None:
    poll_interval = max(5, config.get("poll_interval_seconds", 30))

    while True:
        try:
            items = proxy_channel._fetch_new_messages()
            for item in items:
                sender = item["sender"]
                subject = item.get("subject", "")
                message_id = item.get("message_id", "")
                uid = item.get("uid", "")
                content = item["content"]

                msg_id = uid or message_id
                if not msg_id:
                    msg_id = str(time.time())

                def forward(item=item):
                    try:
                        with proxy_channel._send_lock:
                            future = asyncio.run_coroutine_threadsafe(
                                proxy_channel._send_with_reconnect({
                                    "channel": proxy_channel.channel,
                                    "bot": proxy_channel.bot,
                                    "sender_id": sender,
                                    "chat_id": sender,
                                    "content": content,
                                    "message_id": msg_id,
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                }),
                                proxy_channel._conn_loop,
                            )
                            response = future.result(timeout=300)

                        if response and response.success and response.content:
                            proxy_channel._smtp_send(
                                sender,
                                response.content,
                                subject=f"Re: {subject}" if subject else None,
                                in_reply_to=message_id or None,
                            )
                    except Exception as e:
                        logger.error("Failed to forward message after retries: {}, exiting process", e)
                        os._exit(1)

                t = threading.Thread(target=forward, daemon=True)
                t.start()
        except Exception as e:
            logger.error("Email poll loop error: {}", e)

        time.sleep(poll_interval)


def main() -> None:
    args = _parse_args()
    config = _get_config()

    required = ["imap_host", "imap_username", "imap_password", "smtp_host", "smtp_username", "smtp_password"]
    missing = [f for f in required if not config.get(f)]
    if missing:
        logger.error("Email proxy: missing required config: {}", ", ".join(missing))
        sys.exit(1)

    hub_tcp_host = "127.0.0.1"
    hub_tcp_port = args.hub_tcp_port
    channel = args.channel
    bot = args.bot

    logger.info("Email proxy starting for {}:{}", channel, bot)

    try:
        proxy_channel = EmailProxyChannel(config, hub_tcp_host, hub_tcp_port, channel, bot)
        proxy_channel._connect_tcp()
        logger.info("Registered with Hub via TCP")
        run_email_loop(config, hub_tcp_host, hub_tcp_port, channel, bot, proxy_channel)
    except Exception as e:
        logger.error("Failed to start Email proxy: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        logger.error("Email proxy crashed: {}", traceback.format_exc())
        sys.exit(1)