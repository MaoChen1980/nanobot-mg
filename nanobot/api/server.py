"""Minimal HTTP server for nanobot — settings page only."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

__all__ = ["create_app"]


async def handle_health(request: web.Request) -> web.Response:
    """GET /health"""
    return web.json_response({"status": "ok"})


async def handle_settings_get(request: web.Request) -> web.Response:
    """GET /api/settings"""
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    config = load_config()
    defaults = config.agents.defaults

    provider_name = config.get_provider_name(defaults.model) or defaults.provider
    resolved = provider_name if provider_name != "auto" else "openai"
    provider_cfg = config.get_provider(defaults.model)
    has_key = bool(provider_cfg and provider_cfg.api_key)

    try:
        from nanobot.providers.registry import ALL_PROVIDERS
        providers = [{"name": p.name, "label": p.label} for p in ALL_PROVIDERS]
    except Exception:
        providers = [{"name": "openai", "label": "OpenAI"}]

    return web.json_response({
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


async def handle_config_get(request: web.Request) -> web.Response:
    """GET /api/config — return full config as JSON"""
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    try:
        config = load_config()
        return web.json_response(config.model_dump())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_config_update(request: web.Request) -> web.Response:
    """PUT /api/config — save full config from JSON"""
    try:
        from nanobot.config.loader import load_config, save_config
        from nanobot.config.schema import Config
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        validated = Config.model_validate(data)
        save_config(validated)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": f"Validation failed: {e}"}, status=400)


async def handle_provider_models(request: web.Request) -> web.Response:
    """GET /api/provider-models?provider=X — fetch model list from provider API"""
    provider = request.query.get("provider", "")
    if not provider:
        return web.json_response({"error": "provider required"}, status=400)
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    config = load_config()
    provider_cfg = getattr(config.providers, provider, None)
    if not provider_cfg or not provider_cfg.api_key:
        return web.json_response({"models": []})
    api_key = provider_cfg.api_key
    api_base = (provider_cfg.api_base or "").rstrip("/")
    defaults = {
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
        "deepseek": "https://api.deepseek.com/v1/models",
        "minimax": "https://api.minimax.chat/v1/models",
        "minimax_anthropic": "https://api.minimax.chat/v1/models",
        "minimax_anthropic_cn": "https://api.minimax.chat/v1/models",
        "moonshot": "https://api.moonshot.cn/v1/models",
        "groq": "https://api.groq.com/openai/v1/models",
        "ollama": "http://localhost:11434/v1/models",
        "gemini": "https://generativelanguage.googleapis.com/v1beta3/models",
    }
    url = f"{api_base}/v1/models" if api_base else defaults.get(provider, f"https://api.{provider}.com/v1/models")
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers={"Authorization": f"Bearer {api_key}"}) as resp:
                data = await resp.json()
        models = []
        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data["data"]]
        elif isinstance(data, dict) and "models" in data:
            models = [m["name"] for m in data["models"]]
        return web.json_response({"models": models})
    except Exception as e:
        return web.json_response({"models": [], "error": str(e)})


async def handle_settings_update(request: web.Request) -> web.Response:
    """PUT /api/settings/update"""
    try:
        from nanobot.config.loader import load_config, save_config
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

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
            return web.json_response({"error": f"Failed to save config: {e}"}, status=500)

    defaults = config.agents.defaults
    provider_name = config.get_provider_name(defaults.model) or defaults.provider
    resolved = provider_name if provider_name != "auto" else "openai"
    provider_cfg = config.get_provider(defaults.model)
    has_key = bool(provider_cfg and provider_cfg.api_key)

    try:
        from nanobot.providers.registry import ALL_PROVIDERS
        providers = [{"name": p.name, "label": p.label} for p in ALL_PROVIDERS]
    except Exception:
        providers = [{"name": "openai", "label": "OpenAI"}]

    return web.json_response({
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


async def handle_shutdown(request: web.Request) -> web.Response:
    """POST /api/shutdown — stop proxies then restart the gateway process."""
    import sys
    import os
    import subprocess
    import tempfile
    import threading

    # Stop proxy children first so they don't orphan WS connections.
    proxy_manager = request.app.get("proxy_manager")
    if proxy_manager:
        try:
            await proxy_manager.stop()
        except Exception:
            pass

    def deferred_restart():
        import time
        time.sleep(0.3)
        log_path = os.path.join(tempfile.gettempdir(), "_nanobot_restart.log")
        bat_path = os.path.join(tempfile.gettempdir(), "_nanobot_restart.bat")
        with open(bat_path, "w") as f:
            f.write("@echo off\n")
            f.write(f'echo [%time%] restart batch started >> "{log_path}"\n')
            f.write(f'timeout /t 3 /nobreak >nul\n')
            f.write(f'echo [%time%] launching gateway >> "{log_path}"\n')
            f.write(f'"{sys.executable}" -m nanobot gateway >> "{log_path}" 2>&1\n')
            f.write(f'echo [%time%] exit code %%ERRORLEVEL%% >> "{log_path}"\n')
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=deferred_restart, daemon=True).start()
    return web.json_response({"ok": True, "message": "Gateway restarting"})


def create_app(index_html_path: str | Path = "", proxy_manager=None) -> web.Application:
    """Create aiohttp app with only settings API."""
    app = web.Application()
    app["index_html_path"] = str(index_html_path)
    app["proxy_manager"] = proxy_manager
    # Derive webui public dir from index_html_path (webui/index.html → webui/public)
    public_dir = Path(index_html_path).parent / "public"
    app.router.add_get("/", lambda r: web.FileResponse(r.app["index_html_path"]))
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/provider-models", handle_provider_models)
    app.router.add_get("/api/config", handle_config_get)
    app.router.add_put("/api/config", handle_config_update)
    app.router.add_get("/api/settings", handle_settings_get)
    app.router.add_put("/api/settings/update", handle_settings_update)
    app.router.add_post("/api/shutdown", handle_shutdown)
    # Serve /brand/* from webui/public/brand/
    if public_dir.is_dir():
        app.router.add_static("/brand", public_dir / "brand", follow_symlinks=True)
    return app
