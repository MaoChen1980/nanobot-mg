"""Minimal HTTP server for nanobot — settings page only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

__all__ = ["create_app"]

# Legacy re-exports — moved to nanobot.utils.media_decode
from nanobot.utils.media_decode import FileSizeExceeded as _FileSizeExceeded  # noqa: F401
from nanobot.utils.media_decode import MAX_FILE_SIZE  # noqa: F401
from nanobot.utils.media_decode import save_base64_data_url as _save_base64_data_url  # noqa: F401

# Config cache: keyed by config file mtime, auto-invalidates on save
_config_cache: tuple[float, Any] | None = None

def _cached_config() -> Any:
    global _config_cache
    from nanobot.config.loader import load_config
    from nanobot.config.paths import get_config_path
    path = get_config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    if _config_cache is not None and _config_cache[0] == mtime:
        return _config_cache[1]
    config = load_config()
    _config_cache = (mtime, config)
    return config

# MemoryStore shared instance (workspace never changes at runtime)
_memory_store: Any | None = None

def _get_memory_store(workspace: Path) -> Any:
    global _memory_store
    if _memory_store is None:
        from nanobot.agent.memory_store import MemoryStore
        _memory_store = MemoryStore(workspace)
    return _memory_store


async def handle_health(request: Request) -> Response:
    """GET /health"""
    return JSONResponse({"status": "ok"})


async def handle_workspace_file(request: Request) -> Response:
    """GET /api/workspace/file?path=... — serve a markdown file from workspace with path-traversal guard."""
    from nanobot.config.loader import load_config
    config = _cached_config()
    workspace = config.workspace_path
    file_path = request.query_params.get("path", "memory/MEMORY.md")
    resolved = (workspace / file_path).resolve()
    if not str(resolved).startswith(str(workspace.resolve())):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"content": "", "exists": False})
    try:
        content = resolved.read_text(encoding="utf-8")
    except Exception as e:
        logger.exception("Failed to read workspace file: {}", file_path)
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"content": content, "path": file_path, "exists": True})


async def handle_memory_search(request: Request) -> Response:
    """GET /api/memory/search?q=...&llm=1 — FAISS search with grep fallback."""
    from nanobot.config.loader import load_config

    q = (request.query_params.get("q") or "").strip()
    if not q:
        return JSONResponse({"results": []})

    config = _cached_config()
    workspace = config.workspace_path
    store = _get_memory_store(workspace)

    # Try vector search first
    store.vector_index.load()
    results = store.vector_index.search(q, k=5)

    # Fall back to grep when FAISS index unavailable or empty
    if not results:
        results = _grep_memory(workspace, q)

    resp: dict[str, object] = {"results": results}

    if request.query_params.get("llm") == "1" and results:
        try:
            from nanobot.providers.factory import make_provider

            provider = make_provider(config)
            memory_text = "\n\n".join(
                f"--- {r['source']} ({r['heading']}) ---\n{r['text']}"
                for r in results
            )
            interpretation = await provider.chat_with_retry(
                model=config.agents.defaults.model,
                messages=[
                    {"role": "system", "content": (
                        "You are a memory analyst. Given a user's question and the "
                        "relevant memory fragments retrieved from their personal knowledge base, "
                        "provide a concise interpretation: how each fragment relates to the query, "
                        "what the user was trying to remember, and any patterns or insights."
                    )},
                    {"role": "user", "content": (
                        f"## Query\n{q}\n\n## Retrieved Memories\n{memory_text}"
                    )},
                ],
            )
            resp["interpretation"] = interpretation.content if interpretation else None
        except Exception as e:
            logger.exception("Memory search LLM interpretation failed")
            resp["interpretation"] = f"LLM interpretation unavailable: {e}"

    return JSONResponse(resp)


def _grep_memory(workspace: Path, q: str, k: int = 5) -> list[dict]:
    """Simple grep-based memory search when FAISS index is unavailable."""
    import re
    memory_dir = workspace / "memory"
    if not memory_dir.is_dir():
        return []
    results: list[dict] = []
    for f in sorted(memory_dir.rglob("*.md")):
        if ".vector_index" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            logger.debug("Failed to read memory file for grep: {}", f)
            continue
        lines = text.split("\n")
        # Score: count matching lines (case-insensitive)
        q_lower = q.lower()
        match_lines = [i for i, line in enumerate(lines) if q_lower in line.lower()]
        if not match_lines:
            continue
        score = min(1.0, len(match_lines) / max(1, len(lines)) * 10)
        # Extract context around first match
        start = max(0, match_lines[0] - 2)
        context = "\n".join(lines[start:match_lines[0] + 3])
        rel = str(f.relative_to(memory_dir))
        results.append({
            "source": rel,
            "heading": "",
            "text": context[:500],
            "score": round(score, 4),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:k]


async def handle_memory_rebuild_index(request: Request) -> Response:
    """POST /api/memory/rebuild-index — rebuild FAISS index in background thread."""
    import asyncio
    from nanobot.config.loader import load_config

    config = _cached_config()
    workspace = config.workspace_path

    loop = asyncio.get_event_loop()

    def _build() -> dict:
        from nanobot.agent.memory_store import MemoryStore
        store = MemoryStore(workspace)
        store.build_vector_index()
        return {
            "faiss_available": store.vector_index._index is not None,
            "chunks": len(store.vector_index._chunks),
        }

    try:
        result = await loop.run_in_executor(None, _build)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        logger.exception("Memory rebuild index failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def handle_settings_get(request: Request) -> Response:
    """GET /api/settings"""
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        logger.exception("Failed to import load_config for /api/settings")
        return JSONResponse({"error": str(e)}, status_code=500)

    config = _cached_config()
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
        logger.exception("Failed to import load_config for /api/config")
        return JSONResponse({"error": str(e)}, status_code=500)
    try:
        config = _cached_config()
        return JSONResponse(config.model_dump())
    except Exception as e:
        logger.exception("Failed to load config for /api/config")
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_config_update(request: Request) -> Response:
    """PUT /api/config — save full config from JSON"""
    try:
        from nanobot.config.loader import load_config, save_config
        from nanobot.config.schema import Config
    except Exception as e:
        logger.exception("Failed to import for /api/config update")
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
                if isinstance(ch_cfg, dict) and not ch_cfg.get("enabled", False):
                    await proxy_manager.stop_proxy(key)

        return JSONResponse({"ok": True})
    except Exception as e:
        logger.exception("Config validation failed on update")
        return JSONResponse({"error": f"Validation failed: {e}"}, status_code=400)


async def handle_provider_models(request: Request) -> Response:
    """GET /api/provider-models?provider=X — fetch model list from provider API"""
    provider = request.query_params.get("provider", "")
    if not provider:
        return JSONResponse({"error": "provider required"}, status_code=400)
    try:
        from nanobot.config.loader import load_config
    except Exception as e:
        logger.exception("Failed to import load_config for /api/provider-models")
        return JSONResponse({"error": str(e)}, status_code=500)
    config = _cached_config()
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
        if isinstance(data, dict) and data.get("data"):
            models = [m["id"] for m in data["data"]]
        elif isinstance(data, dict) and "models" in data:
            models = [m["name"] for m in data["models"]]
        return JSONResponse({"models": models})
    except Exception as e:
        logger.exception("Failed to fetch models from provider '{}'", provider)
        return JSONResponse({"models": [], "error": str(e)})


async def handle_settings_update(request: Request) -> Response:
    """PUT /api/settings/update"""
    try:
        from nanobot.config.loader import load_config, save_config
    except Exception as e:
        logger.exception("Failed to import for /api/settings/update")
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    config = _cached_config()
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
            logger.exception("Failed to save config on update")
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
            logger.warning("Failed to stop proxy manager during shutdown")

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
        logger.info("Shutdown via /api/shutdown, exiting with os._exit(0)")
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
            logger.warning("Failed to stop proxy manager during stop")

    def deferred_exit():
        import time
        time.sleep(0.3)
        logger.info("Stop via /api/stop, exiting with os._exit(0)")
        os._exit(0)

    threading.Thread(target=deferred_exit, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Gateway shutting down"})


async def handle_memory_chat(request: Request) -> Response:
    """POST /api/memory/chat — AI chat over memory with SSE streaming (Google AI mode-style)."""
    import asyncio
    import json

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    history = body.get("history", [])
    if not isinstance(history, list):
        return JSONResponse({"error": "history must be a list"}, status_code=400)

    from nanobot.config.loader import load_config

    config = _cached_config()
    workspace = config.workspace_path
    model = config.agents.defaults.model

    # Search memory via FAISS + grep fallback
    store = _get_memory_store(workspace)
    results = store.vector_index.search(message, k=5)
    if not results:
        results = _grep_memory(workspace, message)

    # Build system prompt with search results as context
    sources_data: list[dict] = []
    context_parts: list[str] = []
    for r in results:
        label = f"{r['source']} — {r['heading']}" if r.get("heading") else r["source"]
        context_parts.append(f"### {label} (relevance: {r.get('score', 0):.2f})\n{r['text']}")
        sources_data.append({
            "source": r["source"],
            "heading": r.get("heading", ""),
            "score": r.get("score", 0),
            "text": r["text"][:200],
        })

    context_str = "\n\n".join(context_parts) if context_parts else "No relevant memory found."

    system_prompt = (
        "You are an AI assistant answering questions about the user's personal memory and knowledge base. "
        "Use the retrieved context below to answer. "
        "Cite sources inline using the format `[source: filename]`. "
        "If the context doesn't contain relevant information, say so clearly.\n\n"
        f"## Retrieved Context\n{context_str}"
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    from nanobot.providers.factory import make_provider

    provider = make_provider(config)

    async def event_stream():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_token(token: str):
            await queue.put(token)

        async def run_chat():
            try:
                await provider.chat_stream_with_retry(
                    model=model,
                    messages=messages,
                    on_content_delta=on_token,
                )
            except Exception as e:
                logger.exception("Memory chat streaming failed")
                await queue.put(f"__error__:{e}")
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_chat())

        while True:
            item = await queue.get()
            if item is None:
                break
            if item.startswith("__error__:"):
                payload = json.dumps({"error": item[10:]}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
            else:
                payload = json.dumps({"token": item}, ensure_ascii=False)
                yield f"event: token\ndata: {payload}\n\n"

        # Send sources after tokens complete
        sources_payload = json.dumps({"sources": sources_data}, ensure_ascii=False)
        yield f"event: sources\ndata: {sources_payload}\n\n"
        yield "event: done\ndata: {}\n\n"

        await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
        Route("/api/workspace/file", endpoint=handle_workspace_file),
        Route("/api/memory/search", endpoint=handle_memory_search),
        Route("/api/memory/rebuild-index", endpoint=handle_memory_rebuild_index, methods=["POST"]),
        Route("/api/memory/chat", endpoint=handle_memory_chat, methods=["POST"]),
    ]
    if public_dir.is_dir():
        routes.append(
            Mount("/brand", app=StaticFiles(directory=str(public_dir / "brand")), name="brand")
        )

    app = Starlette(routes=routes)
    app.state.proxy_manager = proxy_manager
    return app
