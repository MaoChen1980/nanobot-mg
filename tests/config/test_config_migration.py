import json
import socket
from unittest.mock import patch

import pytest

from nanobot.config.loader import load_config, save_config
from nanobot.security.network import validate_url_target


def _fake_resolve_async(host: str, results: list[str]):
    """Return an async _resolve_hostname mock for the given host."""
    async def _resolver(hostname: str):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def test_load_config_keeps_max_tokens_and_ignores_legacy_memory_window(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 1234,
                        "memoryWindow": 42,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.max_tokens == 1234
    assert config.agents.defaults.context_window_tokens == 200_000
    assert not hasattr(config.agents.defaults, "memory_window")



def test_onboard_does_not_crash_with_legacy_memory_window(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 3333,
                        "memoryWindow": 50,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("nanobot.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("nanobot.cli.commands.get_workspace_path", lambda _workspace=None: workspace)

    from typer.testing import CliRunner
    from nanobot.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0



def test_load_config_migrates_legacy_my_tool_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": True,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.my.enable is False
    assert config.tools.my.allow_set is True


def test_save_config_rewrites_legacy_my_tool_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": True,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    tools = saved["tools"]
    assert "myEnabled" not in tools
    assert "mySet" not in tools
    assert tools["my"] == {"enable": False, "allowSet": True}


def test_new_my_tool_keys_take_precedence_over_legacy(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": False,
                    "my": {"enable": True, "allowSet": True},
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.my.enable is True
    assert config.tools.my.allow_set is True


# ---------------------------------------------------------------------------
# restrictToWorkspace migration
# ---------------------------------------------------------------------------


def test_load_config_migrates_restrict_to_workspace(tmp_path) -> None:
    """tools.exec.restrictToWorkspace is migrated to tools.restrictToWorkspace."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "tools": {
                "exec": {"restrictToWorkspace": False},
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.tools.restrict_to_workspace is False

    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tools"]["restrictToWorkspace"] is False


# ---------------------------------------------------------------------------
# _migrate_channels — edge cases
# ---------------------------------------------------------------------------


def test_load_config_migrates_flat_channel_to_bots_array(tmp_path) -> None:
    """Flat channel configs (pre multi-bot) are wrapped into bots[0]."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "channels": {
                "feishu": {
                    "enabled": True,
                    "appId": "cli_xxx",
                    "appSecret": "secret",
                },
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    feishu = config.channels.model_dump(by_alias=True)["feishu"]
    assert feishu["enabled"] is True
    assert feishu["bots"] == [{"name": "bot1", "appId": "cli_xxx", "appSecret": "secret"}]


def test_load_config_skips_non_dict_channel_sections(tmp_path) -> None:
    """Non-dict channel sections are skipped without crashing."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "channels": {
                "feishu": "not-a-dict",
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config is not None


def test_load_config_skips_known_top_level_channel_fields(tmp_path) -> None:
    """Top-level ChannelsConfig fields like send_progress are not migrated."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "channels": {
                "sendProgress": False,
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.channels.send_progress is False


def test_load_config_skips_channel_with_existing_bots_array(tmp_path) -> None:
    """Already-migrated bots[] config is not double-migrated."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "channels": {
                "feishu": {
                    "enabled": True,
                    "bots": [{"name": "bot1", "appId": "cli_xxx"}],
                },
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    feishu = config.channels.model_dump(by_alias=True)["feishu"]
    assert feishu["bots"] == [{"name": "bot1", "appId": "cli_xxx"}]


def test_load_config_skips_channel_with_no_bot_fields(tmp_path) -> None:
    """Channel section with only 'enabled' and no bot fields is skipped."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "channels": {
                "telegram": {"enabled": False},
            },
        }),
        encoding="utf-8",
    )

    config = load_config(config_path)
    telegram = config.channels.model_dump(by_alias=True).get("telegram", {})
    assert telegram.get("enabled") is False
    assert "bots" not in telegram


async def test_load_config_resets_ssrf_whitelist_when_next_config_is_empty(tmp_path) -> None:
    whitelisted = tmp_path / "whitelisted.json"
    whitelisted.write_text(
        json.dumps({"tools": {"ssrfWhitelist": ["100.64.0.0/10"]}}),
        encoding="utf-8",
    )
    defaulted = tmp_path / "defaulted.json"
    defaulted.write_text(json.dumps({}), encoding="utf-8")

    load_config(whitelisted)
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("ts.local", ["100.100.1.1"])):
        ok, err = await validate_url_target("http://ts.local/api")
        assert ok, err

    load_config(defaulted)
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("ts.local", ["100.100.1.1"])):
        ok, _ = await validate_url_target("http://ts.local/api")
        assert not ok
