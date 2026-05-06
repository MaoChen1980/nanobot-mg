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
    app.router.add_get("/api/settings", handle_settings_get)
    app.router.add_put("/api/settings/update", handle_settings_update)
    # Serve /brand/* from webui/public/brand/
    if public_dir.is_dir():
        app.router.add_static("/brand", public_dir / "brand", follow_symlinks=True)
    return app
