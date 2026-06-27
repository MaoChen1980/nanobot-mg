"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import pydantic
from loguru import logger
from pydantic import BaseModel

from nanobot.config.schema import Config

# Global variable to store current config path (for multi-instance support)
_current_config_path: Optional[Path] = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".nanobot" / "config.json"


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    _apply_ssrf_whitelist(config)
    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from nanobot.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Optional[Path] = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` (e.g.
    ``ExtractorConfig.cron``) survive; returns the same instance when no
    references are present. Raises ``ValueError`` if a referenced
    variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {}) or tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    # Migrate flat channel configs → bots[] format
    from nanobot.config.schema import ChannelsConfig
    _migrate_channels(data, ChannelsConfig)

    return data


def _migrate_channels(data: dict, channels_model: type) -> None:
    """Convert old flat channel configs (fields at section level) to bots[] format.

    Old format (pre multi-bot):
      "feishu": { "enabled": true, "appId": "...", "appSecret": "..." }

    New format:
      "feishu": { "enabled": true, "bots": [{ "name": "bot1", "appId": "...", ... }] }
    """
    known_fields = set(channels_model.model_fields) if hasattr(channels_model, "model_fields") else set()
    channels = data.get("channels", {})
    for name, section in list(channels.items()):
        if not isinstance(section, dict):
            continue
        # Skip known top-level ChannelsConfig fields (send_progress, etc.)
        if name in known_fields:
            continue
        # Already has bots[] or no bot-level fields → nothing to do
        if "bots" in section:
            continue
        bot_fields = {k: v for k, v in section.items() if k not in ("enabled", "bots")}
        if not bot_fields:
            continue
        # Migrate: wrap flat fields into bots[0]
        bot_fields["name"] = bot_fields.pop("name", "bot1")
        channels[name] = {
            "enabled": section.get("enabled", False),
            "bots": [bot_fields],
        }
