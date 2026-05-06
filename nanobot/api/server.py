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
    provider_cfg = config.get_provider(provider)
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
        import urllib.request
        import json as _json
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
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


def create_app(index_html_path: str | Path = "") -> web.Application:
    """Create aiohttp app with only settings API."""
    app = web.Application()
    app["index_html_path"] = str(index_html_path)
    # Derive webui public dir from index_html_path (webui/index.html → webui/public)
    public_dir = Path(index_html_path).parent / "public"
    app.router.add_get("/", lambda r: web.FileResponse(r.app["index_html_path"]))
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/provider-models", handle_provider_models)
    app.router.add_get("/api/config", handle_config_get)
    app.router.add_put("/api/config", handle_config_update)
    app.router.add_get("/api/settings", handle_settings_get)
    app.router.add_put("/api/settings/update", handle_settings_update)
    # Serve /brand/* from webui/public/brand/
    if public_dir.is_dir():
        app.router.add_static("/brand", public_dir / "brand", follow_symlinks=True)
    return app
