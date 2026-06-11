"""DingTalk bot onboarding — create and configure via device OAuth flow.

Uses DingTalk Open Platform's device registration flow (same as
dingtalk-openclaw-connector) to create a new bot, then writes config.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.loader import get_config_path

# ---------------------------------------------------------------------------
# DingTalk API endpoints
# ---------------------------------------------------------------------------
OAPI_DINGTALK = "https://oapi.dingtalk.com"

REGISTRATION_INIT_URL = f"{OAPI_DINGTALK}/app/registration/init"
REGISTRATION_BEGIN_URL = f"{OAPI_DINGTALK}/app/registration/begin"
REGISTRATION_POLL_URL = f"{OAPI_DINGTALK}/app/registration/poll"

REGISTRATION_SOURCE = "openClaw"

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RegistrationResponse:
    device_code: str
    verification_uri_complete: str
    expires_in: int = 7200
    interval: int = 3


@dataclass
class RegistrationResult:
    app_id: str
    app_secret: str

    brand: str = "dingtalk"


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------


def _post_json(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """POST JSON body, return parsed JSON response."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DingTalk API error {exc.code}: {resp_body}") from exc


# ---------------------------------------------------------------------------
# Device OAuth — app registration flow
# ---------------------------------------------------------------------------


def begin_registration() -> RegistrationResponse:
    """Start a device-flow registration.

    Two-step handshake::

        1. POST /app/registration/init   → nonce
        2. POST /app/registration/begin  → device_code + verification_uri

    Returns a ``RegistrationResponse`` with device code and verification URL.
    """
    # Step 1: init → nonce
    init_resp = _post_json(REGISTRATION_INIT_URL, {"source": REGISTRATION_SOURCE})
    nonce = str(init_resp.get("nonce") or "")
    if not nonce:
        raise RuntimeError("DingTalk registration init failed: missing nonce")

    # Step 2: begin → device_code, verification_uri_complete
    begin_resp = _post_json(REGISTRATION_BEGIN_URL, {"nonce": nonce})
    device_code = str(begin_resp.get("device_code") or "")
    verification_uri = str(begin_resp.get("verification_uri_complete") or "")

    if not device_code:
        raise RuntimeError("DingTalk registration begin: missing device_code")
    if not verification_uri:
        raise RuntimeError("DingTalk registration begin: missing verification_uri_complete")

    return RegistrationResponse(
        device_code=device_code,
        verification_uri_complete=verification_uri,
        expires_in=int(begin_resp.get("expires_in", 7200)),
        interval=max(int(begin_resp.get("interval", 3)), 2),
    )


def poll_registration(device_code: str) -> RegistrationResult | None:
    """Poll registration status once.

    Returns a ``RegistrationResult`` on success, ``None`` if still pending
    (status=WAITING).  Raises ``RuntimeError`` on explicit failure or expiry.
    """
    resp = _post_json(REGISTRATION_POLL_URL, {"device_code": device_code})

    status = str(resp.get("status") or "").upper()
    client_id = str(resp.get("client_id") or "")
    client_secret = str(resp.get("client_secret") or "")

    if status == "SUCCESS":
        if not client_id or not client_secret:
            raise RuntimeError("DingTalk poll: SUCCESS but missing credentials")
        return RegistrationResult(app_id=client_id, app_secret=client_secret)

    if status == "WAITING":
        return None

    if status in ("FAIL", "EXPIRED"):
        reason = str(resp.get("fail_reason") or status)
        raise RuntimeError(f"DingTalk authorization {status.lower()}: {reason}")

    return None  # UNKNOWN — keep polling


def wait_for_registration(
    device_code: str,
    reg: RegistrationResponse,
) -> RegistrationResult:
    """Block-poll registration until success or timeout.

    Raises
    ------
    TimeoutError
        If the device code expires before the user completes the flow.
    RuntimeError
        On explicit failure or expiry.
    """
    deadline = time.time() + reg.expires_in
    interval = max(reg.interval, 2)

    while time.time() < deadline:
        time.sleep(interval)

        result = poll_registration(device_code)
        if result is not None:
            return result

        logger.debug("DingTalk poll: WAITING (sleep={}s)", interval)

    raise TimeoutError("Timed out waiting for DingTalk QR code scan")


# ---------------------------------------------------------------------------
# Config writing
# ---------------------------------------------------------------------------

_BOT_CONFIG_TEMPLATE: dict[str, Any] = {
    "domain": "dingtalk",
    "allowFrom": [],
    "groupPolicy": "mention",
}


def write_config(
    app_id: str,
    app_secret: str,
    *,
    bot_name: str = "dingtalk-bot",
    config_path: str | None = None,
) -> Path:
    """Persist bot credentials into ``~/.nanobot/config.json``.

    Returns the path to the saved config file.
    """
    path = Path(config_path).expanduser().resolve() if config_path else get_config_path()

    if path.exists():
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    else:
        data = {}

    channels = data.setdefault("channels", {})
    dingtalk = channels.setdefault("dingtalk", {})
    dingtalk["enabled"] = True
    bots: list[dict[str, Any]] = dingtalk.setdefault("bots", [])

    # Avoid duplicate entries for the same app_id
    for bot in bots:
        if bot.get("clientId") == app_id:
            logger.warning("Bot {} already registered, updating secret", app_id)
            bot["clientSecret"] = app_secret
            break
    else:
        bot_entry: dict[str, Any] = {
            "name": bot_name,
            "clientId": app_id,
            "clientSecret": app_secret,
            **_BOT_CONFIG_TEMPLATE,
        }
        bots.append(bot_entry)

    from nanobot.config.schema import Config

    config = Config.model_validate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            config.model_dump(mode="json", by_alias=True),
            f,
            indent=2,
            ensure_ascii=False,
        )

    return path


# ---------------------------------------------------------------------------
# Public entry point — called from the CLI
# ---------------------------------------------------------------------------


def run_onboard_dingtalk(
    *,
    bot_name: str = "dingtalk-bot",
    config_path: str | None = None,
    print_fn: Any = print,
) -> None:
    """Full onboarding flow: register → scan → write config.

    Parameters
    ----------
    bot_name:
        Display name for the bot entry in config.
    config_path:
        Path to ``config.json``.  Uses the default location when ``None``.
    print_fn:
        Callable for user-facing output.
    """
    # Step 1 — begin registration
    print_fn("Connecting to DingTalk Open Platform ...")
    reg = begin_registration()

    # Step 2 — show QR code
    url = reg.verification_uri_complete
    print_fn("")
    print_fn("Scan this QR code with your DingTalk app to create your bot:")
    print_fn("")

    _render_qrcode(url, print_fn)

    print_fn("")
    print_fn(f"Or open this URL in your browser: {url}")
    print_fn("")

    if webbrowser.open(url):
        print_fn("Browser opened automatically.")
    else:
        print_fn("(Could not open browser automatically — use the URL above.)")
    print_fn("")

    # Step 3 — poll for completion
    print_fn("Waiting for scan ...")
    result = wait_for_registration(reg.device_code, reg)

    print_fn(f"Bot created: {result.app_id}")

    # Step 4 — write config
    path = write_config(
        result.app_id,
        result.app_secret,
        bot_name=bot_name,
        config_path=config_path,
    )
    print_fn(f"Config written to {path}")

    # Step 5 — next steps
    print_fn("")
    print_fn("Your DingTalk bot is configured and ready!")
    print_fn("")
    print_fn("Next steps:")
    print_fn(f"  1. Start the gateway: nanobot gateway{_fmt_config_hint(config_path)}")
    print_fn("  2. Add the bot to a DingTalk chat and start talking!")
    print_fn("")


def _fmt_config_hint(config_path: str | None) -> str:
    if config_path:
        return f" --config {config_path}"
    return ""


def _render_qrcode(url: str, print_fn: Any = print) -> None:
    """Render a QR code to the terminal, degrading gracefully."""
    try:
        import qrcode
    except ImportError:
        print_fn("[qrcode library not available, open the URL instead]")
        return

    try:
        qr = qrcode.QRCode(border=2, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        for line in qr.get_matrix():
            chars = "".join("██" if cell else "  " for cell in line)
            print_fn(chars)
    except Exception as exc:
        logger.debug("QR code rendering failed: {}", exc)
        print_fn(f"[render QR manually with URL: {url}]")
