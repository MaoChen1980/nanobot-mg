"""Tests for GatewayApplication — gateway service orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.config.schema import Config
from nanobot.cron.types import CronJob, CronPayload
from nanobot.gateway.app import GatewayApplication


@pytest.fixture
def config() -> Config:
    return Config()


def _make_mocked_app(config: Config | None = None) -> GatewayApplication:
    """Create a GatewayApplication with all internal services mocked."""
    app = GatewayApplication(config or Config())
    app.bus = MagicMock()
    app.bus.publish_outbound = AsyncMock()
    app.provider = MagicMock()
    app.nanobot_db = MagicMock()
    app.session_manager = MagicMock()
    app.session_manager.get_or_create = MagicMock(return_value=MagicMock())
    app.session_manager.save = MagicMock()
    app.cron = MagicMock()
    app.cron.start = AsyncMock()
    app.cron.status = MagicMock(return_value={"jobs": 3})
    app.cron.on_job = None
    app.agent = MagicMock()
    app.agent.model = "test-model"
    app.agent.dream = MagicMock()
    app.agent.close_mcp = AsyncMock()
    app.agent.stop = MagicMock()
    message_tool = MagicMock(spec=MessageTool)
    app.agent.tools = {"message": message_tool}
    app.agent.sessions = MagicMock()
    app.agent.sessions.flush_all = MagicMock(return_value=2)
    app.channels = MagicMock()
    app.channels.enabled_channels = ["telegram", "slack"]
    app.proxy_manager = MagicMock()
    app.proxy_manager.stop = AsyncMock()
    app.heartbeat = MagicMock()
    app.heartbeat.start = AsyncMock()
    app.api_server = None
    app.hub_server = None
    return app


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_config(self, config: Config) -> None:
        app = GatewayApplication(config)
        assert app.config is config
        assert app.port == config.gateway.port

    def test_port_override(self, config: Config) -> None:
        app = GatewayApplication(config, port=9999)
        assert app.port == 9999


# ---------------------------------------------------------------------------
# _init_services
# ---------------------------------------------------------------------------


class TestInitServices:
    def test_creates_all_components(self, config: Config) -> None:
        """Verify _init_services creates the expected services."""
        app = GatewayApplication(config)

        with (
            patch("nanobot.bus.queue.MessageBus") as mb,
            patch("nanobot.providers.factory.build_provider_snapshot") as bps,
            patch("nanobot.agent.db.NanobotDB") as ndb,
            patch("nanobot.session.manager.SessionManager") as sm,
            patch("nanobot.cron.service.CronService") as cs,
            patch("nanobot.agent.loop.AgentLoop") as al,
            patch("nanobot.bus.manager.ChannelManager") as chm,
            patch("nanobot.proxy.manager.ProxyManager") as pm,
            patch("nanobot.heartbeat.service.HeartbeatService") as hb,
            patch("nanobot.utils.gitstore.sync_workspace_templates"),
        ):
            provider_snapshot = MagicMock()
            provider_snapshot.model = "test-model"
            provider_snapshot.context_window_tokens = 128000
            provider_snapshot.signature = ("test",)
            bps.return_value = provider_snapshot

            app._init_services()

        assert app.bus is not None
        assert app.provider is not None
        assert app.nanobot_db is not None
        assert app.session_manager is not None
        assert app.cron is not None
        assert app.agent is not None
        assert app.channels is not None
        assert app.proxy_manager is not None
        assert app.heartbeat is not None

    def test_value_error_exits(self, config: Config) -> None:
        """When build_provider_snapshot raises ValueError, SystemExit is raised."""
        app = GatewayApplication(config)

        with (
            patch("nanobot.bus.queue.MessageBus"),
            patch(
                "nanobot.providers.factory.build_provider_snapshot",
                side_effect=ValueError("bad provider"),
            ),
        ):
            with pytest.raises(SystemExit):
                app._init_services()

    def test_init_uses_provider(self, config: Config) -> None:
        """Provider is resolved from provider_snapshot.provider."""
        app = GatewayApplication(config)

        with (
            patch("nanobot.bus.queue.MessageBus"),
            patch("nanobot.providers.factory.build_provider_snapshot") as bps,
            patch("nanobot.agent.db.NanobotDB"),
            patch("nanobot.session.manager.SessionManager"),
            patch("nanobot.cron.service.CronService"),
            patch("nanobot.agent.loop.AgentLoop"),
            patch("nanobot.bus.manager.ChannelManager"),
            patch("nanobot.proxy.manager.ProxyManager"),
            patch("nanobot.heartbeat.service.HeartbeatService"),
            patch("nanobot.utils.gitstore.sync_workspace_templates"),
        ):
            provider_snapshot = MagicMock()
            provider_snapshot.model = "test-model"
            provider_snapshot.context_window_tokens = 128000
            provider_snapshot.signature = ("test",)
            bps.return_value = provider_snapshot

            app._init_services()

        assert app.provider is provider_snapshot.provider


# ---------------------------------------------------------------------------
# _migrate_cron_store (static)
# ---------------------------------------------------------------------------


class TestMigrateCronStore:
    def test_migrates_when_legacy_exists_and_new_does_not(self, tmp_path: Path) -> None:
        legacy_path = tmp_path / "legacy_cron" / "jobs.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text('{"jobs": []}')
        new_path = tmp_path / "workspace" / "cron" / "jobs.json"

        config = Config()
        config.agents.defaults.workspace = str(tmp_path / "workspace")

        with patch("nanobot.config.paths.get_cron_dir", return_value=tmp_path / "legacy_cron"):
            GatewayApplication._migrate_cron_store(config)

        assert not legacy_path.exists()
        assert new_path.exists()

    def test_skips_when_legacy_missing(self, tmp_path: Path) -> None:
        config = Config()
        config.agents.defaults.workspace = str(tmp_path / "workspace")

        with patch("nanobot.config.paths.get_cron_dir", return_value=tmp_path / "legacy_cron"):
            GatewayApplication._migrate_cron_store(config)

        new_path = tmp_path / "workspace" / "cron" / "jobs.json"
        assert not new_path.exists()

    def test_skips_when_new_already_exists(self, tmp_path: Path) -> None:
        legacy_path = tmp_path / "legacy_cron" / "jobs.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text('{"jobs": []}')
        new_path = tmp_path / "workspace" / "cron" / "jobs.json"
        new_path.parent.mkdir(parents=True)
        new_path.write_text('{"jobs": [1, 2, 3]}')

        config = Config()
        config.agents.defaults.workspace = str(tmp_path / "workspace")

        with patch("nanobot.config.paths.get_cron_dir", return_value=tmp_path / "legacy_cron"):
            GatewayApplication._migrate_cron_store(config)

        assert legacy_path.exists()  # not moved
        assert new_path.read_text() == '{"jobs": [1, 2, 3]}'  # unchanged


# ---------------------------------------------------------------------------
# _get_bots_list (static)
# ---------------------------------------------------------------------------


class TestGetBotsList:
    def test_dict_with_bots(self) -> None:
        section = {"bots": [{"name": "bot1"}]}
        assert GatewayApplication._get_bots_list(section) == [{"name": "bot1"}]

    def test_dict_without_bots(self) -> None:
        assert GatewayApplication._get_bots_list({"enabled": True}) == []

    def test_object_with_extra_bots(self) -> None:
        obj = MagicMock()
        obj.__pydantic_extra__ = {"bots": ["bot_a"]}
        assert GatewayApplication._get_bots_list(obj) == ["bot_a"]

    def test_object_without_extra_bots(self) -> None:
        obj = MagicMock()
        obj.__pydantic_extra__ = {}
        assert GatewayApplication._get_bots_list(obj) == []

    def test_object_no_extra(self) -> None:
        obj = MagicMock(spec=object)
        assert GatewayApplication._get_bots_list(obj) == []


# ---------------------------------------------------------------------------
# _merge_bot_config (static)
# ---------------------------------------------------------------------------


class TestMergeBotConfig:
    def test_dict_base_with_dict_bot(self) -> None:
        section = {"enabled": True, "api_key": "x"}
        bot_item = {"name": "bot1", "api_key": "y"}
        name, merged = GatewayApplication._merge_bot_config(section, bot_item)
        assert name == "bot1"
        assert merged == {"enabled": True, "api_key": "y", "name": "bot1"}

    def test_dict_base_with_str_bot(self) -> None:
        section = {"enabled": True}
        name, merged = GatewayApplication._merge_bot_config(section, "my_bot")
        assert name == "my_bot"
        assert merged == {"enabled": True}

    def test_object_base_with_dict_bot(self) -> None:
        section = MagicMock()
        section.model_dump = MagicMock(return_value={"enabled": True})
        section.__pydantic_extra__ = {"extra_key": "v"}
        bot_item = {"name": "bot_x", "extra_key": "overridden"}
        name, merged = GatewayApplication._merge_bot_config(section, bot_item)
        assert name == "bot_x"
        assert merged == {"enabled": True, "extra_key": "overridden", "name": "bot_x"}

    def test_object_base_with_str_bot(self) -> None:
        section = MagicMock()
        section.model_dump = MagicMock(return_value={"enabled": True})
        section.__pydantic_extra__ = {}
        name, merged = GatewayApplication._merge_bot_config(section, "simple_bot")
        assert name == "simple_bot"
        assert merged == {"enabled": True}

    def test_object_no_model_dump_falls_back_to_dict(self) -> None:
        class _PlainSection:
            def __init__(self):
                self.__pydantic_extra__ = {}
            def __iter__(self):
                return iter([])

        section = _PlainSection()
        bot_item = {"name": "n"}
        name, merged = GatewayApplication._merge_bot_config(section, bot_item)
        assert name == "n"


# ---------------------------------------------------------------------------
# _wire_callbacks — _channel_session_key
# ---------------------------------------------------------------------------


class TestChannelSessionKey:
    def test_unified_session(self, config: Config) -> None:
        config.agents.defaults.unified_session = True
        app = _make_mocked_app(config)
        app._wire_callbacks()

    def test_per_channel_key(self, config: Config) -> None:
        config.agents.defaults.unified_session = False
        app = _make_mocked_app(config)
        app._wire_callbacks()


# ---------------------------------------------------------------------------
# _wire_callbacks — _deliver_to_channel
# ---------------------------------------------------------------------------


class TestDeliverToChannel:
    @pytest.fixture
    def app(self, config: Config) -> GatewayApplication:
        app = _make_mocked_app(config)
        app._wire_callbacks()
        return app

    def test_delivers_without_record(self, app: GatewayApplication) -> None:
        from nanobot.bus.events import OutboundMessage

        delivery = app.agent.tools["message"].set_send_callback.call_args[0][0]
        msg = OutboundMessage(channel="telegram", chat_id="user-1", content="hello")

        import asyncio
        asyncio.run(delivery(msg))

        app.bus.publish_outbound.assert_awaited_once_with(msg)

    def test_deliver_records_session(self, app: GatewayApplication) -> None:
        from nanobot.bus.events import OutboundMessage

        delivery = app.agent.tools["message"].set_send_callback.call_args[0][0]
        msg = OutboundMessage(channel="telegram", chat_id="user-1", content="hello")

        import asyncio
        asyncio.run(delivery(msg, record=True, session_key="tg:u1"))

        app.session_manager.get_or_create.assert_called_once_with("tg:u1")

    def test_deliver_skip_cli_channel(self, app: GatewayApplication) -> None:
        from nanobot.bus.events import OutboundMessage

        delivery = app.agent.tools["message"].set_send_callback.call_args[0][0]
        msg = OutboundMessage(channel="cli", chat_id="local", content="hello")

        import asyncio
        asyncio.run(delivery(msg, record=True))

        app.session_manager.get_or_create.assert_not_called()

    def test_deliver_skip_empty_content(self, app: GatewayApplication) -> None:
        from nanobot.bus.events import OutboundMessage

        delivery = app.agent.tools["message"].set_send_callback.call_args[0][0]
        msg = OutboundMessage(channel="telegram", chat_id="user-1", content="   ")

        import asyncio
        asyncio.run(delivery(msg, record=True))

        app.session_manager.get_or_create.assert_not_called()

    def test_record_from_metadata(self, app: GatewayApplication) -> None:
        from nanobot.bus.events import OutboundMessage

        delivery = app.agent.tools["message"].set_send_callback.call_args[0][0]
        msg = OutboundMessage(
            channel="telegram", chat_id="user-1", content="hi",
            metadata={"_record_channel_delivery": True},
        )

        import asyncio
        asyncio.run(delivery(msg))

        app.session_manager.get_or_create.assert_called_once()


# ---------------------------------------------------------------------------
# _print_startup_status
# ---------------------------------------------------------------------------


class TestPrintStartupStatus:
    def test_with_enabled_channels(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.channels.enabled_channels = ["telegram", "slack"]
        app.cron.status = MagicMock(return_value={"jobs": 2})
        with patch("nanobot.gateway.app.console.print") as cp:
            app._print_startup_status()
            assert cp.call_count >= 2

    def test_no_channels_warning(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.channels.enabled_channels = []
        app.cron.status = MagicMock(return_value={"jobs": 0})
        with patch("nanobot.gateway.app.console.print") as cp:
            app._print_startup_status()
            texts = [c[0][0] for c in cp.call_args_list if c[0]]
            assert any("No channels enabled" in str(t) for t in texts)

    def test_cron_jobs_printed(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.channels.enabled_channels = ["telegram"]
        app.cron.status = MagicMock(return_value={"jobs": 5})
        with patch("nanobot.gateway.app.console.print") as cp:
            app._print_startup_status()
            texts = [c[0][0] for c in cp.call_args_list if c[0]]
            assert any("5 scheduled jobs" in str(t) for t in texts)


# ---------------------------------------------------------------------------
# _register_dream_job
# ---------------------------------------------------------------------------


class TestRegisterDreamJob:
    def test_registers_dream_job(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.dream.model = "default-model"
        app.agent.dream.max_batch_size = 10
        app.agent.dream.max_iterations = 1
        app.agent.dream.annotate_line_ages = False

        with patch("nanobot.gateway.app.console.print"):
            app._register_dream_job()

        app.cron.register_system_job.assert_called_once()
        job = app.cron.register_system_job.call_args[0][0]
        assert job.id == "dream"
        assert job.name == "dream"

    def test_model_override(self, config: Config) -> None:
        config.agents.defaults.dream.model_override = "claude-opus"
        app = _make_mocked_app(config)

        with patch("nanobot.gateway.app.console.print"):
            app._register_dream_job()

        assert app.agent.dream.model == "claude-opus"


# ---------------------------------------------------------------------------
# _shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_without_init_does_not_crash(self) -> None:
        app = GatewayApplication(Config())
        import asyncio
        asyncio.run(app._shutdown())

    def test_full_shutdown_with_all_services(self, config: Config) -> None:
        app = _make_mocked_app(config)

        import asyncio
        asyncio.run(app._shutdown())

        app.agent.close_mcp.assert_awaited_once()
        app.agent.stop.assert_called_once()
        app.agent.sessions.flush_all.assert_called_once()
        app.heartbeat.stop.assert_called_once()
        app.cron.stop.assert_called_once()
        app.proxy_manager.stop.assert_awaited_once()

    def test_shutdown_skips_partial_services(self, config: Config) -> None:
        app = GatewayApplication(config)
        app.agent = MagicMock()
        app.agent.close_mcp = AsyncMock()
        app.agent.sessions = MagicMock()
        app.agent.sessions.flush_all = MagicMock(return_value=0)
        app.cron = MagicMock()
        app.heartbeat = None

        import asyncio
        asyncio.run(app._shutdown())

        app.agent.close_mcp.assert_awaited_once()
        app.cron.stop.assert_called_once()

    def test_shutdown_closes_hub_server(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.hub_server = AsyncMock()

        import asyncio
        asyncio.run(app._shutdown())

        app.hub_server.stop.assert_awaited_once()

    def test_shutdown_stops_api_server(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.api_server = MagicMock()

        import asyncio
        asyncio.run(app._shutdown())

        assert app.api_server.should_exit is True
        app.api_server.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# _start_all
# ---------------------------------------------------------------------------


class TestStartAll:
    def test_starts_services_and_gathers(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.run = AsyncMock()
        app.proxy_manager.start_monitoring = AsyncMock()

        with (
            patch("nanobot.proxy.hub.HubTCPServer") as hub,
            patch.object(app, "_run_api_server", return_value=AsyncMock()),
        ):
            hub.return_value.start = AsyncMock()
            import asyncio
            asyncio.run(app._start_all())

        app.cron.start.assert_called_once()
        app.heartbeat.start.assert_called_once()
        hub.assert_called_once()

    def test_open_browser_task_added(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.run = AsyncMock()
        app.open_browser_url = "http://localhost:8080"
        app.proxy_manager.start_monitoring = AsyncMock()

        with (
            patch("nanobot.proxy.hub.HubTCPServer") as hub,
            patch.object(app, "_run_api_server", return_value=AsyncMock()),
            patch("asyncio.gather", AsyncMock()) as gather,
        ):
            hub.return_value.start = AsyncMock()
            import asyncio
            asyncio.run(app._start_all())

        tasks = gather.call_args[0]
        assert len(tasks) == 4  # agent.run, proxy_monitor, api_server, browser_open


# ---------------------------------------------------------------------------
# _run_api_server
# ---------------------------------------------------------------------------


class TestRunApiServer:
    def test_creates_uvicorn_server(self, config: Config) -> None:
        app = _make_mocked_app(config)

        with (
            patch("uvicorn.Config") as uvi_config,
            patch("uvicorn.Server") as uvi_server,
            patch("nanobot.api.server.create_app") as ca,
            patch("nanobot.gateway.app.console.print"),
        ):
            uvi_server.return_value.serve = AsyncMock()
            import asyncio
            asyncio.run(app._run_api_server("127.0.0.1", 8080))

        uvi_config.assert_called_once()
        assert app.api_server is uvi_server.return_value
        assert app.api_server.install_signal_handlers() is None


# ---------------------------------------------------------------------------
# _open_browser_when_ready
# ---------------------------------------------------------------------------


class TestOpenBrowserWhenReady:
    """_open_browser_when_ready — waits for port then opens browser."""

    @staticmethod
    async def _oc_ok(*_a, **_kw):
        return MagicMock(), MagicMock()

    async def _oc_fail_then_ok(self, *_a, **_kw):
        if not self._oc_called:
            self._oc_called = True
            raise OSError()
        return MagicMock(), MagicMock()

    def test_opens_browser_on_connection(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.open_browser_url = "http://localhost:8080"

        with (
            patch("asyncio.open_connection", side_effect=self._oc_ok),
            patch("webbrowser.open") as wb_open,
            patch("nanobot.gateway.app.console.print"),
        ):
            import asyncio
            asyncio.run(app._open_browser_when_ready())

        wb_open.assert_called_once_with("http://localhost:8080")

    def test_retries_on_oserror(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.open_browser_url = "http://localhost:8080"
        self._oc_called = False

        with (
            patch("asyncio.open_connection", side_effect=self._oc_fail_then_ok),
            patch("webbrowser.open") as wb_open,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("nanobot.gateway.app.console.print"),
        ):
            import asyncio
            asyncio.run(app._open_browser_when_ready())

        wb_open.assert_called_once_with("http://localhost:8080")

    def test_browser_open_failure_logged(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.open_browser_url = "http://localhost:8080"

        with (
            patch("asyncio.open_connection", side_effect=self._oc_ok),
            patch("webbrowser.open") as wb_open,
            patch("nanobot.gateway.app.console.print") as cp,
        ):
            wb_open.side_effect = Exception("no browser")
            import asyncio
            asyncio.run(app._open_browser_when_ready())

        texts = [c[0][0] for c in cp.call_args_list if c[0]]
        assert any("Could not open browser" in str(t) for t in texts)


# ---------------------------------------------------------------------------
# _spawn_proxy_processes
# ---------------------------------------------------------------------------


class TestSpawnProxyProcesses:
    def test_skips_disabled_channels(self, config: Config) -> None:
        app = _make_mocked_app(config)
        ch_type = type(app.config.channels)
        app.config.channels.__pydantic_extra__ = {}

        with (
            patch.object(ch_type, "model_fields", {}),
            patch("nanobot.gateway.app.console.print"),
        ):
            app._spawn_proxy_processes()

        app.proxy_manager.spawn.assert_not_called()

    def test_spawns_for_enabled_channel_with_bots(self, config: Config) -> None:
        app = _make_mocked_app(config)
        ch_type = type(app.config.channels)
        app.config.channels.__pydantic_extra__ = {
            "custom_ch": {
                "enabled": True, "bots": [{"name": "bot1", "token": "x"}],
            }
        }

        with (
            patch.object(ch_type, "model_fields", {}),
            patch("nanobot.gateway.app.console.print"),
        ):
            app._spawn_proxy_processes()

        app.proxy_manager.spawn.assert_called_once_with(
            "custom_ch", "bot1",
            {"enabled": True, "bots": [{"name": "bot1", "token": "x"}],
             "name": "bot1", "token": "x"},
        )

    def test_skips_channel_without_bots(self, config: Config) -> None:
        app = _make_mocked_app(config)
        ch_type = type(app.config.channels)
        app.config.channels.__pydantic_extra__ = {
            "custom_ch": {"enabled": True},
        }

        with (
            patch.object(ch_type, "model_fields", {}),
            patch("nanobot.gateway.app.console.print"),
        ):
            app._spawn_proxy_processes()

        app.proxy_manager.spawn.assert_not_called()

    def test_handles_pydantic_extra_sections(self, config: Config) -> None:
        app = _make_mocked_app(config)
        ch_type = type(app.config.channels)
        app.config.channels.__pydantic_extra__ = {
            "custom_ch": {
                "enabled": True,
                "type": "webhook",
                "bots": ["default"],
            }
        }

        with (
            patch.object(ch_type, "model_fields", {}),
            patch("nanobot.gateway.app.console.print"),
        ):
            app._spawn_proxy_processes()

        app.proxy_manager.spawn.assert_called_once_with(
            "custom_ch", "default",
            {"enabled": True, "type": "webhook", "bots": ["default"]},
        )


# ---------------------------------------------------------------------------
# _async_run — error paths
# ---------------------------------------------------------------------------


class TestAsyncRun:
    def test_calls_full_lifecycle(self, config: Config) -> None:
        app = _make_mocked_app(config)

        with (
            patch.object(app, "_init_services") as init_svc,
            patch.object(app, "_wire_callbacks") as wire_cb,
            patch.object(app, "_print_startup_status") as print_st,
            patch.object(app, "_register_dream_job") as reg_dream,
            patch.object(app, "_start_all") as start_all,
            patch.object(app, "_shutdown") as shutdown,
        ):
            import asyncio
            asyncio.run(app._async_run())

        init_svc.assert_called_once()
        wire_cb.assert_called_once()
        print_st.assert_called_once()
        reg_dream.assert_called_once()
        start_all.assert_awaited_once()
        shutdown.assert_awaited_once()

    def test_keyboard_interrupt(self, config: Config) -> None:
        app = _make_mocked_app(config)
        exc = KeyboardInterrupt()

        with (
            patch.object(app, "_init_services"),
            patch.object(app, "_start_all", side_effect=exc),
            patch.object(app, "_shutdown") as shutdown,
        ):
            import asyncio
            asyncio.run(app._async_run())

        shutdown.assert_awaited_once()

    def test_generic_exception(self, config: Config) -> None:
        app = _make_mocked_app(config)
        exc = RuntimeError("boom")

        with (
            patch.object(app, "_init_services"),
            patch.object(app, "_start_all", side_effect=exc),
            patch.object(app, "_shutdown") as shutdown,
        ):
            import asyncio
            asyncio.run(app._async_run())

        shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# on_cron_job — the handler wired inside _wire_callbacks (lines 239-320)
# ---------------------------------------------------------------------------


class TestOnCronJob:
    """Tests for the on_cron_job closure defined inside _wire_callbacks."""

    async def test_dream_job_success(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.dream.run = AsyncMock()
        app._wire_callbacks()
        job = CronJob(id="dream", name="dream")

        result = await app.cron.on_job(job)

        assert result is None
        app.agent.dream.run.assert_awaited_once()

    async def test_dream_job_exception(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.dream.run = AsyncMock(side_effect=ValueError("boom"))
        app._wire_callbacks()
        with patch("nanobot.gateway.app.logger.exception") as log_exc:
            job = CronJob(id="dream", name="dream")
            result = await app.cron.on_job(job)

        assert result is None
        log_exc.assert_called_once_with("Dream cron job failed")

    async def test_reminder_without_delivery(self, config: Config) -> None:
        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Hello!"))
        app._wire_callbacks()
        job = CronJob(
            id="r1", name="reminder",
            payload=CronPayload(message="Remind me", deliver=False),
        )

        result = await app.cron.on_job(job)

        assert result == "Hello!"
        app.agent.process_direct.assert_awaited_once()
        app.bus.publish_outbound.assert_not_called()

    async def test_reminder_with_delivery_and_already_sent(self, config: Config) -> None:
        """When _sent_in_turn is True, returns early without evaluate_response."""
        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Hello!"))
        app.agent.tools["message"]._sent_in_turn = True

        with patch("nanobot.utils.evaluator.evaluate_response") as mock_eval:
            app._wire_callbacks()
            job = CronJob(
                id="r1", name="reminder",
                payload=CronPayload(
                    message="Test", deliver=True,
                    channel="cli", to="user1",
                ),
            )
            result = await app.cron.on_job(job)

        assert result == "Hello!"
        mock_eval.assert_not_called()

    async def test_reminder_delivery_evaluate_notifies(self, config: Config) -> None:
        """evaluate_response returns True -> message is delivered."""
        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Important!"))
        app.agent.tools["message"]._sent_in_turn = False

        app._wire_callbacks()
        with patch(
            "nanobot.utils.evaluator.evaluate_response",
            AsyncMock(return_value=True),
        ):
            job = CronJob(
                id="r1", name="reminder",
                payload=CronPayload(
                    message="Test", deliver=True,
                    channel="cli", to="user1",
                ),
            )
            result = await app.cron.on_job(job)

        assert result == "Important!"
        app.bus.publish_outbound.assert_awaited_once()

    async def test_reminder_delivery_evaluate_skips(self, config: Config) -> None:
        """evaluate_response returns False -> message is NOT delivered."""
        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Routine"))
        app.agent.tools["message"]._sent_in_turn = False

        app._wire_callbacks()
        with patch(
            "nanobot.utils.evaluator.evaluate_response",
            AsyncMock(return_value=False),
        ):
            job = CronJob(
                id="r1", name="reminder",
                payload=CronPayload(
                    message="Test", deliver=True,
                    channel="cli", to="user1",
                ),
            )
            result = await app.cron.on_job(job)

        assert result == "Routine"
        app.bus.publish_outbound.assert_not_called()

    async def test_cron_context_set_and_reset(self, config: Config) -> None:
        """set_cron_context / reset_cron_context are called when cron tool exists."""
        from nanobot.agent.tools.cron import CronTool

        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Hi"))
        cron_tool = MagicMock(spec=CronTool)
        cron_tool.set_cron_context.return_value = "ctx-token"
        cron_tool.set_current_job_id.return_value = "job-token"
        app.agent.tools["cron"] = cron_tool
        app._wire_callbacks()
        job = CronJob(
            id="r1", name="reminder",
            payload=CronPayload(message="Test", deliver=False),
        )

        result = await app.cron.on_job(job)

        assert result == "Hi"
        cron_tool.set_cron_context.assert_called_once_with(True)
        cron_tool.set_current_job_id.assert_called_once_with("r1")
        cron_tool.reset_cron_context.assert_called_once_with("ctx-token")
        cron_tool.reset_current_job_id.assert_called_once_with("job-token")

    async def test_no_message_tool_still_processes(self, config: Config) -> None:
        """Reminder works even when agent has no 'message' tool."""
        app = _make_mocked_app(config)
        app.agent.process_direct = AsyncMock(return_value=MagicMock(content="Hi"))
        app.agent.tools = {}
        app._wire_callbacks()
        job = CronJob(
            id="r1", name="reminder",
            payload=CronPayload(message="Test", deliver=False),
        )

        result = await app.cron.on_job(job)

        assert result == "Hi"


# ---------------------------------------------------------------------------
# _spawn_proxy_processes — underscore-prefixed skip (line 515)
# ---------------------------------------------------------------------------


class TestSpawnProxyUnderscore:
    """Channel names starting with _ are skipped in _spawn_proxy_processes."""

    def test_skips_underscore_prefixed_channel(self, config: Config) -> None:
        app = _make_mocked_app(config)
        ch_type = type(app.config.channels)
        app.config.channels.__pydantic_extra__ = {
            "_internal": {
                "enabled": True, "bots": [{"name": "bot1", "token": "x"}],
            },
            "visible_ch": {
                "enabled": True, "bots": [{"name": "bot2", "token": "y"}],
            },
        }

        with (
            patch.object(ch_type, "model_fields", {}),
            patch("nanobot.gateway.app.console.print"),
        ):
            app._spawn_proxy_processes()

        app.proxy_manager.spawn.assert_called_once_with(
            "visible_ch", "bot2",
            {
                "enabled": True,
                "bots": [{"name": "bot2", "token": "y"}],
                "name": "bot2", "token": "y",
            },
        )
