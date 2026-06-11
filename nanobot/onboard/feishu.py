"""Feishu bot onboarding — create and configure via device OAuth flow.

Uses Feishu Open Platform's device OAuth endpoint (same flow as lark-cli)
to register a new app, then enables bot capability and writes config.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.loader import get_config_path

# ---------------------------------------------------------------------------
# Feishu API endpoints (China mainland)
# ---------------------------------------------------------------------------
ACCOUNTS_FEISHU = "https://accounts.feishu.cn"
OPEN_FEISHU = "https://open.feishu.cn"

APP_REGISTRATION_URL = f"{ACCOUNTS_FEISHU}/oauth/v1/app/registration"
TENANT_TOKEN_URL = f"{OPEN_FEISHU}/open-apis/auth/v3/tenant_access_token/internal"
APPLICATION_ABILITY_URL = (
    f"{OPEN_FEISHU}/open-apis/application/v7/applications/{{app_id}}/ability"
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RegistrationResponse:
    device_code: str
    user_code: str
    expires_in: int = 300
    interval: int = 5


@dataclass
class RegistrationResult:
    app_id: str
    app_secret: str

    brand: str = "feishu"


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    """POST ``application/x-www-form-urlencoded``, return parsed JSON.

    Unlike standard OAuth device flow, Feishu returns HTTP 400 with
    ``authorization_pending`` during polling instead of HTTP 200.
    To support this, HTTP errors are returned as-is rather than raised,
    letting callers inspect the response body for error codes.
    """
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        try:
            return dict(json.loads(resp_body))
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError(f"Feishu API error {exc.code}: {resp_body}") from exc


def _post_json(url: str, data: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    """POST JSON body, return parsed JSON response."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu API error {exc.code}: {resp_body}") from exc


def _patch_json(url: str, data: dict[str, Any], token: str) -> dict[str, Any]:
    """PATCH JSON body, return parsed JSON response."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu API error {exc.code}: {resp_body}") from exc


# ---------------------------------------------------------------------------
# Device OAuth — app registration flow
# ---------------------------------------------------------------------------


def begin_registration() -> RegistrationResponse:
    """POST ``action=begin`` — start the device OAuth flow.

    Returns a ``RegistrationResponse`` with the device code and verification
    URL fragment.  The caller should render a QR code from the verification
    URL for the user to scan with their Feishu app.
    """
    resp = _post_form(
        APP_REGISTRATION_URL,
        {
            "action": "begin",
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id tenant_brand",
        },
    )
    return RegistrationResponse(
        device_code=resp["device_code"],
        user_code=resp["user_code"],
        expires_in=resp.get("expires_in", 300),
        interval=resp.get("interval", 5),
    )


def poll_registration(device_code: str, reg: RegistrationResponse) -> RegistrationResult:
    """Poll ``action=poll`` until the user scans the QR code.

    Raises
    ------
    TimeoutError
        If the device code expires before the user completes the flow.
    RuntimeError
        If the user explicitly denies the request.
    """
    deadline = time.time() + reg.expires_in
    interval = max(reg.interval, 5)

    while time.time() < deadline:
        time.sleep(interval)

        resp = _post_form(
            APP_REGISTRATION_URL,
            {"action": "poll", "device_code": device_code},
        )

        client_id = resp.get("client_id") or ""
        client_secret = resp.get("client_secret") or ""

        if client_id:
            result = RegistrationResult(
                app_id=client_id,
                app_secret=client_secret,
            )
            user_info = resp.get("user_info")
            if isinstance(user_info, dict):
                result.brand = user_info.get("tenant_brand", "feishu")
            return result

        # Error handling based on common OAuth device-flow error codes
        err = resp.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = min(interval + 5, 60)
            continue
        if err == "access_denied":
            raise RuntimeError("Authorization denied by user")
        if err in ("expired_token", "invalid_grant"):
            raise RuntimeError("Device code expired — please try again")

        logger.debug("Poll response (unhandled): {}", resp)

    raise TimeoutError("Timed out waiting for QR code scan")


# ---------------------------------------------------------------------------
# Bot capability
# ---------------------------------------------------------------------------


def enable_bot_capability(app_id: str, app_secret: str) -> None:
    """Enable the Bot capability on the newly created app.

    This calls ``PATCH /application/v7/applications/{app_id}/ability`` with
    ``bot.enable=True``.
    """
    token = _post_json(
        TENANT_TOKEN_URL,
        {"app_id": app_id, "app_secret": app_secret},
    ).get("tenant_access_token")
    if not token:
        raise RuntimeError("Failed to obtain tenant_access_token")

    url = APPLICATION_ABILITY_URL.format(app_id=app_id)
    resp = _patch_json(url, {"bot": {"enable": True}}, token)
    code = resp.get("code", -1)
    if code != 0:
        raise RuntimeError(f"Failed to enable bot capability: code={code} msg={resp.get('msg', '')}")


# ---------------------------------------------------------------------------
# Config writing
# ---------------------------------------------------------------------------

_BOT_CONFIG_TEMPLATE: dict[str, Any] = {
    "domain": "feishu",
    "allowFrom": [],
    "groupPolicy": "mention",
    "streaming": True,
    "renderMode": "card",
}


def write_config(
    app_id: str,
    app_secret: str,
    *,
    bot_name: str = "feishu-bot",
    config_path: str | None = None,
) -> Path:
    """Persist bot credentials into ``~/.nanobot/config.json``.

    If the file doesn't exist yet a minimal config skeleton is created first.

    Returns the path to the saved config file.
    """
    path = Path(config_path).expanduser().resolve() if config_path else get_config_path()

    # Load existing config as raw dict so we can modify the feishu section
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    else:
        data = {}

    # Deep-merge feishu bot entry
    channels = data.setdefault("channels", {})
    feishu = channels.setdefault("feishu", {})
    feishu["enabled"] = True
    bots: list[dict[str, Any]] = feishu.setdefault("bots", [])

    # Avoid duplicate entries for the same app_id
    for bot in bots:
        if bot.get("appId") == app_id:
            logger.warning("Bot {} already registered, updating secret", app_id)
            bot["appSecret"] = app_secret
            break
    else:
        bot_entry: dict[str, Any] = {
            "name": bot_name,
            "appId": app_id,
            "appSecret": app_secret,
            **_BOT_CONFIG_TEMPLATE,
        }
        bots.append(bot_entry)

    # Validate & persist through nanobot's own infrastructure
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


def run_onboard_feishu(
    *,
    bot_name: str = "feishu-bot",
    config_path: str | None = None,
    print_fn: Any = print,
) -> None:
    """Full onboarding flow: register → scan → enable → write config.

    Parameters
    ----------
    bot_name:
        Display name for the bot entry in config.
    config_path:
        Path to ``config.json``.  Uses the default location when ``None``.
    print_fn:
        Callable for user-facing output (useful for testing or Rich wrapping).
    """
    # Step 1 — begin registration
    print_fn("Connecting to Feishu Open Platform ...")
    reg = begin_registration()

    # Step 2 — show QR code
    verification_url = f"https://open.feishu.cn/page/cli?user_code={reg.user_code}"
    print_fn("")
    print_fn("Scan this QR code with your Feishu app to create your bot:")
    print_fn("")

    # Generate and render QR code
    _render_qrcode(verification_url, print_fn)

    print_fn("")
    print_fn(f"Or open this URL in your browser: {verification_url}")
    print_fn("")

    if webbrowser.open(verification_url):
        print_fn("Browser opened automatically.")
    else:
        print_fn("(Could not open browser automatically — use the URL above.)")
    print_fn("")

    # Step 3 — poll for completion
    print_fn("Waiting for scan ...")
    result = poll_registration(reg.device_code, reg)

    print_fn(f"App created: {result.app_id}")

    # Step 4 — enable bot capability
    print_fn("Enabling bot capability ...")
    try:
        enable_bot_capability(result.app_id, result.app_secret)
        print_fn("Bot capability enabled")
    except RuntimeError as exc:
        print_fn(f"Warning: could not enable bot automatically: {exc}")
        print_fn("You can enable it manually in the Feishu developer console.")

    # Step 5 — write config
    path = write_config(
        result.app_id,
        result.app_secret,
        bot_name=bot_name,
        config_path=config_path,
    )
    print_fn(f"Config written to {path}")

    # Step 6 — next steps
    print_fn("")
    print_fn("Your Feishu bot is configured and ready!")
    print_fn("")
    print_fn("Next steps:")
    print_fn(f"  1. Start the gateway: nanobot gateway{_fmt_config_hint(config_path)}")
    print_fn("  2. (one-time) In the Feishu Developer Console:")
    print_fn("     a. Import permissions → batch-paste the JSON from the docs")
    print_fn("     b. Subscribe to event: im.message.receive_v1")
    print_fn("     c. Create a version and publish the app")
    print_fn("  3. Add the bot to a chat and start talking!")
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
