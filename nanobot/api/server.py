"""Minimal HTTP server for nanobot — settings page only."""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

__all__ = ["create_app"]

# Legacy re-exports — moved to nanobot.utils.media_decode
from nanobot.utils.media_decode import FileSizeExceeded as _FileSizeExceeded  # noqa: F401
from nanobot.utils.media_decode import MAX_FILE_SIZE  # noqa: F401
from nanobot.utils.media_decode import save_base64_data_url as _save_base64_data_url  # noqa: F401


async def handle_health(request: Request) -> Response:
    """GET /health"""
    return JSONResponse({"status": "ok"})


async def handle_settings_get(request: Request) -> Response:
    """GET /api/settings"""
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    config = load_config()
    defaults = config.agents.defaults

    provider_name = config.get_provider_name(defaults.model) or defaults.provider
    resolved = provider_name if provider_name != "auto" else "openai"
    provider_cfg = config.get_provider(defaults.model)
    has_key = bool(provider_cfg and provider_cfg.api_key)

    from nanobot.providers.registry import PROVIDERS
    providers = [{"name": p.name, "label": p.label} for p in PROVIDERS]

    return JSONResponse({
        "agent": {
            "model": defaults.model or "",
            "provider": defaults.provider or "auto",
            "resolved_provider": resolved,
            "has_api_key": has_key,
        },
        "providers": providers,
        "runtime": {"config_path": ""},
        "requires_restart": False,
    })


async def handle_config_get(request: Request) -> Response:
    """GET /api/config — return full config as JSON"""
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    try:
        config = load_config()
        return JSONResponse(config.model_dump())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_config_update(request: Request) -> Response:
    """PUT /api/config — save full config from JSON"""
    try:
        from nanobot.config.loader import load_config, save_config
        from nanobot.config.schema import Config
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        validated = Config.model_validate(data)
        save_config(validated)

        # Reconcile running proxies: stop any whose channel is now disabled
        channels_data = data.get("channels", {})
        proxy_manager = getattr(request.app.state, "proxy_manager", None)
        if proxy_manager:
            for key in list(proxy_manager.get_proxy_keys()):
                ch, _ = key.split(":", 1)
                ch_cfg = channels_data.get(ch, {})
                if not ch_cfg.get("enabled", False):
                    proxy_manager.stop_proxy(key)

        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": f"Validation failed: {e}"}, status_code=400)


async def handle_provider_models(request: Request) -> Response:
    """GET /api/provider-models?provider=X — fetch model list from provider API"""
    provider = request.query_params.get("provider", "")
    if not provider:
        return JSONResponse({"error": "provider required"}, status_code=400)
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    config = load_config()
    provider_cfg = getattr(config.providers, provider, None)
    if not provider_cfg or not provider_cfg.api_key:
        return JSONResponse({"models": []})
    api_key = provider_cfg.api_key
    api_base = (provider_cfg.api_base or "").rstrip("/")
    defaults = {
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
        "deepseek": "https://api.deepseek.com/v1/models",
        "minimax": "https://api.minimax.chat/v1/models",
        "minimax_anthropic": "https://api.minimax.chat/v1/models",
        "minimax_anthropic_cn": "https://api.minimax.chat/v1/models",
        "minimax_cn": "https://api.minimax.chat/v1/models",
        "moonshot": "https://api.moonshot.cn/v1/models",
        "groq": "https://api.groq.com/openai/v1/models",
        "ollama": "http://localhost:11434/v1/models",
        "gemini": "https://generativelanguage.googleapis.com/v1beta3/models",
    }
    if api_base:
        # Avoid double /v1 when api_base already ends with /v1
        prefix = api_base.rstrip("/")
        url = f"{prefix}/models" if prefix.endswith("/v1") else f"{prefix}/v1/models"
    else:
        url = defaults.get(provider, f"https://api.{provider}.com/v1/models")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            data = resp.json()
        models = []
        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data["data"]]
        elif isinstance(data, dict) and "models" in data:
            models = [m["name"] for m in data["models"]]
        return JSONResponse({"models": models})
    except Exception as e:
        return JSONResponse({"models": [], "error": str(e)})


async def handle_settings_update(request: Request) -> Response:
    """PUT /api/settings/update"""
    try:
        from nanobot.config.loader import load_config, save_config
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    config = load_config()
    updated = False

    if "model" in data and data["model"]:
        config.agents.defaults.model = data["model"]
        updated = True
    if "provider" in data and data["provider"]:
        config.agents.defaults.provider = data["provider"]
        updated = True

    if updated:
        try:
            save_config(config)
        except Exception as e:
            return JSONResponse({"error": f"Failed to save config: {e}"}, status_code=500)

    defaults = config.agents.defaults
    provider_name = config.get_provider_name(defaults.model) or defaults.provider
    resolved = provider_name if provider_name != "auto" else "openai"
    provider_cfg = config.get_provider(defaults.model)
    has_key = bool(provider_cfg and provider_cfg.api_key)

    from nanobot.providers.registry import PROVIDERS
    providers = [{"name": p.name, "label": p.label} for p in PROVIDERS]

    return JSONResponse({
        "agent": {
            "model": defaults.model or "",
            "provider": defaults.provider or "auto",
            "resolved_provider": resolved,
            "has_api_key": has_key,
        },
        "providers": providers,
        "runtime": {"config_path": ""},
        "requires_restart": updated,
    })


async def handle_shutdown(request: Request) -> Response:
    """POST /api/shutdown — stop proxies then restart the gateway process."""
    import os
    import subprocess
    import sys
    import threading

    # Stop proxy children first so they don't orphan WS connections.
    proxy_manager = getattr(request.app.state, "proxy_manager", None)
    if proxy_manager:
        try:
            await proxy_manager.stop()
        except Exception:
            pass

    def deferred_restart():
        import time
        time.sleep(0.3)

        # Reconstruct gateway command, forwarding --config if present
        restart_cmd = [sys.executable, "-m", "nanobot", "gateway"]
        for i, a in enumerate(sys.argv):
            if a in ("--config", "-c") and i + 1 < len(sys.argv):
                restart_cmd.extend([a, sys.argv[i + 1]])

        # Cross-platform delayed restart: spawn a Python child that waits
        # then execs the gateway, while this process exits immediately.
        delay_cmd = (
            f"import time,subprocess,sys;"
            f"time.sleep(3);"
            f"sys.exit(subprocess.call({restart_cmd!r}))"
        )
        subprocess.Popen(
            [sys.executable, "-c", delay_cmd],
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=deferred_restart, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Gateway restarting"})


async def handle_stop(request: Request) -> Response:
    """POST /api/stop — stop proxies then exit the gateway process."""
    import os
    import sys
    import threading

    proxy_manager = getattr(request.app.state, "proxy_manager", None)
    if proxy_manager:
        try:
            await proxy_manager.stop()
        except Exception:
            pass

    def deferred_exit():
        import time
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=deferred_exit, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Gateway shutting down"})


def create_app(index_html_path: str | Path = "", proxy_manager=None) -> Starlette:
    """Create Starlette app with only settings API."""
    public_dir = Path(index_html_path).parent / "public"

    async def homepage(request: Request) -> Response:
        return FileResponse(str(index_html_path))

    routes = [
        Route("/", endpoint=homepage),
        Route("/health", endpoint=handle_health),
        Route("/api/provider-models", endpoint=handle_provider_models),
        Route("/api/config", endpoint=handle_config_get),
        Route("/api/config", endpoint=handle_config_update, methods=["PUT"]),
        Route("/api/settings", endpoint=handle_settings_get),
        Route("/api/settings/update", endpoint=handle_settings_update, methods=["PUT"]),
        Route("/api/shutdown", endpoint=handle_shutdown, methods=["POST"]),
        Route("/api/stop", endpoint=handle_stop, methods=["POST"]),
    ]
    if public_dir.is_dir():
        routes.append(
            Mount("/brand", app=StaticFiles(directory=str(public_dir / "brand")), name="brand")
        )

    app = Starlette(routes=routes)
    app.state.proxy_manager = proxy_manager
    return app
