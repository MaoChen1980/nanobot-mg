"""Gateway application — orchestrates services for the nanobot gateway."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console

from nanobot import __logo__, __version__
from nanobot.config.paths import is_default_workspace
from nanobot.config.schema import Config
from nanobot.utils.gitstore import sync_workspace_templates

console = Console()


def _looks_like_traceback(line: str) -> bool:
    """Check if a non-JSON line is part of a traceback."""
    return (line.startswith("Traceback")
            or line.startswith('  File "')
            or line.startswith("    ")  # indented code in traceback
            or line.startswith("  ") and any(
                kw in line for kw in ("Error:", "Exception:")
            ))


class GatewayApplication:
    """Concrete gateway application that starts and manages all services."""

    def __init__(
        self,
        config: Config,
        *,
        port: int | None = None,
        open_browser_url: str | None = None,
    ):
        self.config = config
        self.port = port if port is not None else config.gateway.port
        self.open_browser_url = open_browser_url

        # Services — initialized during run()
        self.bus = None
        self.provider = None
        self.provider_snapshot = None
        self.nanobot_db = None
        self.session_manager = None
        self.cron = None
        self.agent = None
        self.channels = None
        self.proxy_manager = None
        self.heartbeat = None
        self.api_server = None
        self.hub_server = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Synchronous entry point — creates services, starts gateway, blocks until shutdown."""
        asyncio.run(self._async_run())

    # ------------------------------------------------------------------
    # Async main
    # ------------------------------------------------------------------

    async def _async_run(self) -> None:
        display_host = "127.0.0.1" if self.config.gateway.host in {"0.0.0.0", "::"} else self.config.gateway.host
        url = f"http://{display_host}:{self.port}"
        console.print(
            f"{__logo__} Starting nanobot gateway version {__version__} "
            f"on port {self.port}..."
        )
        console.print(f"[green]✓[/green] WebUI at [underline]{url}[/underline]")
        sync_workspace_templates(self.config.workspace_path)

        self._init_services()
        self._wire_callbacks()
        self._print_startup_status()
        self._register_dream_job()
        self._register_log_check_job()

        try:
            await self._start_all()
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except Exception:
            import traceback

            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            logger.exception("Gateway crashed unexpectedly")
            console.print(traceback.format_exc())
        finally:
            await self._shutdown()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_services(self) -> None:
        """Create all service instances."""
        from nanobot.agent.db import NanobotDB
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.manager import ChannelManager
        from nanobot.bus.queue import MessageBus
        from nanobot.cron.service import CronService
        from nanobot.heartbeat.service import HeartbeatService
        from nanobot.providers.factory import (
            build_provider_snapshot,
            load_provider_snapshot,
        )
        from nanobot.session.manager import SessionManager

        self.bus = MessageBus()

        try:
            self.provider_snapshot = build_provider_snapshot(self.config)
        except ValueError as exc:
            logger.error("Provider init failed: {}", exc)
            console.print(f"[red]Error: {exc}[/red]")
            raise SystemExit(1) from exc
        self.provider = self.provider_snapshot.provider

        self.nanobot_db = NanobotDB(
            Path.home() / ".nanobot" / "nanobot.db",
            workspace=self.config.workspace_path,
        )
        self.session_manager = SessionManager(
            self.config.workspace_path, db=self.nanobot_db
        )

        # Preserve existing single-workspace installs, but keep custom workspaces clean.
        if is_default_workspace(self.config.workspace_path):
            self._migrate_cron_store(self.config)

        cron_store_path = self.config.workspace_path / "cron" / "jobs.json"
        self.cron = CronService(cron_store_path)

        self.agent = AgentLoop(
            bus=self.bus,
            provider=self.provider,
            workspace=self.config.workspace_path,
            model=self.provider_snapshot.model,
            max_iterations=self.config.agents.defaults.max_tool_iterations,
            context_window_tokens=self.provider_snapshot.context_window_tokens,
            web_config=self.config.tools.web,
            context_block_limit=self.config.agents.defaults.context_block_limit,
            max_tool_result_chars=self.config.agents.defaults.max_tool_result_chars,
            provider_retry_mode=self.config.agents.defaults.provider_retry_mode,
            exec_config=self.config.tools.exec,
            cron_service=self.cron,
            restrict_to_workspace=self.config.tools.restrict_to_workspace,
            session_manager=self.session_manager,
            mcp_servers=self.config.tools.mcp_servers,
            channels_config=self.config.channels,
            timezone=self.config.agents.defaults.timezone,
            unified_session=self.config.agents.defaults.unified_session,
            disabled_skills=self.config.agents.defaults.disabled_skills,
            session_ttl_minutes=self.config.agents.defaults.session_ttl_minutes,
            consolidation_ratio=self.config.agents.defaults.consolidation_ratio,
            tools_config=self.config.tools,
            provider_snapshot_loader=load_provider_snapshot,
            provider_signature=self.provider_snapshot.signature,
            db=self.nanobot_db,
        )

        self.channels = ChannelManager(self.config, self.bus)

        # Proxy processes for out-of-process channels
        from nanobot.config.loader import get_config_path as _get_cfg_path
        from nanobot.proxy.manager import ProxyManager

        proxy_tcp_port = self.port + 1
        self.proxy_manager = ProxyManager(
            f"http://127.0.0.1:{self.port}",
            proxy_tcp_port=proxy_tcp_port,
            config_path=str(_get_cfg_path()),
        )
        ProxyManager._set_pid_file(
            str(self.config.workspace_path / "gateway.pid")
        )
        ProxyManager.cleanup_orphans()
        ProxyManager._save_gateway_pid()
        self._spawn_proxy_processes()

        hb_cfg = self.config.gateway.heartbeat
        self.heartbeat = HeartbeatService(
            agent_loop=self.agent,
            interval_s=hb_cfg.interval_s,
            enabled=hb_cfg.enabled,
        )

    def _wire_callbacks(self) -> None:
        """Connect cross-component callbacks (message tool, cron, etc.)."""
        from nanobot.agent.loop import UNIFIED_SESSION_KEY
        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool
        from nanobot.bus.events import OutboundMessage

        def _channel_session_key(channel: str, chat_id: str) -> str:
            return (
                UNIFIED_SESSION_KEY
                if self.config.agents.defaults.unified_session
                else f"{channel}:{chat_id}"
            )

        async def _deliver_to_channel(
            msg: OutboundMessage,
            *,
            record: bool = False,
            session_key: str | None = None,
        ) -> None:
            metadata = dict(msg.metadata or {})
            record = record or bool(
                metadata.pop("_record_channel_delivery", False)
            )
            if metadata != (msg.metadata or {}):
                msg = OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=msg.content,
                    reply_to=msg.reply_to,
                    media=msg.media,
                    metadata=metadata,
                    buttons=msg.buttons,
                )
            if (
                record
                and msg.channel != "cli"
                and msg.content.strip()
                and hasattr(self.session_manager, "get_or_create")
                and hasattr(self.session_manager, "save")
            ):
                key = session_key or _channel_session_key(
                    msg.channel, msg.chat_id
                )
                session = self.session_manager.get_or_create(key)
                session.add_message(
                    "assistant", msg.content, _channel_delivery=True
                )
                self.session_manager.save(session)

            # Proxy channels: deliver via proxy TCP connection
            if msg.channel.startswith("proxy:"):
                proxy_key = msg.channel[len("proxy:"):]
                deliver_msg = {
                    "type": "deliver",
                    "chat_id": msg.chat_id,
                    "content": msg.content,
                }
                if not await self.proxy_manager.deliver_to_proxy(
                    proxy_key, deliver_msg
                ):
                    logger.warning(
                        "Failed to deliver to proxy {}, message dropped",
                        proxy_key,
                    )
                return

            await self.bus.publish_outbound(msg)

        message_tool = getattr(self.agent, "tools", {}).get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_send_callback(_deliver_to_channel)

        # Cron job handler
        async def on_cron_job(job: Any) -> str | None:
            from nanobot.agent.tools.cron import CronTool
            from nanobot.agent.tools.message import MessageTool
            from nanobot.utils.evaluator import evaluate_response

            if job.name == "dream":
                try:
                    await self.agent.dream.run()
                    logger.info("Dream cron job completed")
                except Exception:
                    logger.exception("Dream cron job failed")
                return None

            if job.name == "log_check":
                await self._run_log_check(_deliver_to_channel)
                return None

            # Check if this is a test/dry-run execution
            is_test_mode = isinstance(getattr(self.agent.tools.get("cron"), "_test_mode", None), object)

            reminder_note = (
                "The scheduled time has arrived. Deliver this reminder to the user now, "
                "as a brief and natural message in their language. Speak directly to them — "
                "do not narrate progress, summarize, include user IDs, or add status reports "
                "like 'Done' or 'Reminded'.\n\n"
                f"Reminder: {job.payload.message}\n\n"
                "You can use `cron` tool to manage this job:\n"
                f"- `cron action=update job_id={job.id} message=\"...\"` — update the reminder for next run\n"
                f"- `cron action=list` — check job status\n"
                f"- `cron action=remove job_id={job.id}` — cancel this job"
            )

            cron_tool = self.agent.tools.get("cron")
            cron_token = None
            cron_job_token = None
            if isinstance(cron_tool, CronTool):
                # Determine if we should show progress
                dry_run = job.payload.deliver is False  # dry_run mode
                cron_token = cron_tool.set_cron_context(True, dry_run=dry_run)
                cron_job_token = cron_tool.set_current_job_id(job.id)

            # Build progress callback for visible execution
            async def _progress(step: str, done: bool = False) -> None:
                # In test/dry-run mode, progress is captured for display
                # In normal mode, progress is silent unless testing
                pass

            message_record_token = None
            if isinstance(message_tool, MessageTool):
                message_record_token = (
                    message_tool.set_record_channel_delivery(True)
                )

            # Wire progress callback: capture agent tool steps for display
            async def _visible_progress(
                content: str, *, tool_hint: bool = False, tool_events: list = None
            ) -> None:
                if isinstance(cron_tool, CronTool):
                    log = cron_tool.get_execution_log()
                    if tool_events:
                        for ev in tool_events:
                            if ev.get("event") == "start":
                                tool_name = ev.get("tool", "?")
                                log.append(f"  [Tool] {tool_name}()")
                    elif content:
                        # thought / hint content
                        line = content.strip().split("\n")[0][:80]
                        if line:
                            log.append(f"  [Thought] {line}")

            async def _silent(content: str, *, tool_hint: bool = False, tool_events: list = None) -> None:
                pass

            # Determine on_progress: use visible in test mode, silent otherwise
            is_test = getattr(cron_tool, "_test_mode", None) and cron_tool._test_mode.get()
            on_progress = _visible_progress if is_test else _silent

            try:
                resp = await self.agent.process_direct(
                    reminder_note,
                    session_key=f"cron:{job.id}",
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to or "direct",
                    on_progress=on_progress,
                )
            finally:
                if isinstance(cron_tool, CronTool) and cron_token is not None:
                    cron_tool.reset_cron_context(cron_token)
                if isinstance(cron_tool, CronTool) and cron_job_token is not None:
                    cron_tool.reset_current_job_id(cron_job_token)
                if (
                    isinstance(message_tool, MessageTool)
                    and message_record_token is not None
                ):
                    message_tool.reset_record_channel_delivery(
                        message_record_token
                    )

            response = resp.content if resp else ""

            # In test mode: append execution log to result
            if is_test and isinstance(cron_tool, CronTool):
                log = cron_tool.get_execution_log()
                if log:
                    response = "[Test Execution Log]\n" + "\n".join(log) + "\n\n[Result]\n" + response

            # Test/dry-run: return result but don't deliver to user
            if not job.payload.deliver:
                return response

            if (
                job.payload.deliver
                and isinstance(message_tool, MessageTool)
                and message_tool._sent_in_turn
            ):
                return response

            if job.payload.deliver and job.payload.to and response:
                should_notify = await evaluate_response(
                    response,
                    reminder_note,
                    self.provider,
                    self.agent.model,
                )
                if should_notify:
                    await _deliver_to_channel(
                        OutboundMessage(
                            channel=job.payload.channel or "cli",
                            chat_id=job.payload.to,
                            content=response,
                            metadata=dict(job.payload.channel_meta),
                        ),
                        record=True,
                        session_key=job.payload.session_key,
                    )
            return response

        self.cron.on_job = on_cron_job

    def _print_startup_status(self) -> None:
        """Print enabled channels, cron, and heartbeat info."""
        if self.channels.enabled_channels:
            console.print(
                f"[green]✓[/green] Channels enabled: "
                f"{', '.join(self.channels.enabled_channels)}"
            )
        else:
            logger.warning("No channels enabled")
            console.print("[yellow]Warning: No channels enabled[/yellow]")

        cron_status = self.cron.status()
        if cron_status["jobs"] > 0:
            console.print(
                f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs"
            )

        console.print(
            f"[green]✓[/green] Heartbeat: every "
            f"{self.config.gateway.heartbeat.interval_s}s"
        )

    def _register_dream_job(self) -> None:
        """Register the Dream system cron job."""
        from nanobot.cron.types import CronJob, CronPayload

        dream_cfg = self.config.agents.defaults.dream
        if dream_cfg.model_override:
            self.agent.dream.model = dream_cfg.model_override
        self.agent.dream.max_batch_size = dream_cfg.max_batch_size
        self.agent.dream.max_iterations = dream_cfg.max_iterations
        self.agent.dream.annotate_line_ages = dream_cfg.annotate_line_ages

        self.cron.register_system_job(
            CronJob(
                id="dream",
                name="dream",
                schedule=dream_cfg.build_schedule(
                    self.config.agents.defaults.timezone
                ),
                payload=CronPayload(kind="system_event"),
            )
        )
        console.print(
            f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}"
        )

    def _register_log_check_job(self) -> None:
        """Register the log check system cron job (every 2 hours)."""
        from nanobot.cron.types import CronJob, CronPayload, CronSchedule

        self.cron.register_system_job(
            CronJob(
                id="log_check",
                name="log_check",
                schedule=CronSchedule(kind="every", every_ms=7_200_000),
                payload=CronPayload(kind="system_event"),
            )
        )
        console.print("[green]✓[/green] Log check: every 2 hours")

    async def _run_log_check(self, deliver_fn) -> None:
        """Check JSONL log for new ERROR/CRITICAL entries and alert active sessions."""
        from nanobot.bus.events import OutboundMessage
        from nanobot.config.paths import get_data_dir
        from nanobot.utils.logging import _COMMIT as current_commit

        log_name = self.config.logging.file
        if not log_name:
            return
        log_path = get_data_dir() / log_name
        if not log_path.exists():
            return

        # Read last-check timestamp (ISO format, no byte offset)
        cursor_path = get_data_dir() / ".log_check_cursor"
        last_check_ts: datetime | None = None
        if cursor_path.exists():
            try:
                raw = cursor_path.read_text().strip()
                last_check_ts = datetime.fromisoformat(raw)
            except (ValueError, TypeError, OSError):
                pass

        now = datetime.now(timezone.utc)
        two_days_ago = now - timedelta(days=2)

        # Read all lines and iterate backwards (newest first)
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.exception("Log check: failed to read log file")
            return

        # Traceback lines (non-JSON) appear after the ERROR in original order.
        # Since we iterate reversed, they show up *before* their ERROR line.
        # Accumulate them and attach when we reach the owning ERROR entry.
        pending_tb: list[str] = []
        new_errors: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            # Non-JSON line — could be traceback context
            if not line.startswith("{"):
                if _looks_like_traceback(line):
                    pending_tb.append(line)
                continue

            # JSON line — flush pending traceback if this is its ERROR owner
            if pending_tb:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    pending_tb.clear()
                    continue
                if entry.get("l") in ("ERROR", "CRITICAL"):
                    entry["_traceback"] = list(reversed(pending_tb))
                else:
                    entry = None  # skip; traceback was orphaned
                pending_tb.clear()
                if entry is None:
                    continue
            else:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

            ts_str = entry.get("t", "")
            if not ts_str:
                continue
            try:
                entry_ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue

            # Stop scanning entries older than 2 days
            if entry_ts < two_days_ago:
                break

            # Only check entries from the current deployment
            if entry.get("v") != current_commit:
                continue

            if entry.get("l") in ("ERROR", "CRITICAL"):
                if last_check_ts is None or entry_ts > last_check_ts:
                    new_errors.append(entry)

        # Save cursor regardless — prevents re-alerting old errors next run
        cursor_path.write_text(now.isoformat())

        if not new_errors:
            return

        # Don't alert on the very first run (would report all historical errors)
        if last_check_ts is None:
            logger.info("Log check: first run, skipping alert")
            return

        # Build concise alert
        MAX_SHOWN = 15
        shown = new_errors[:MAX_SHOWN]
        parts = [
            f"[Log Alert] {len(new_errors)} new error(s):"
        ]
        for entry in shown:
            ts = entry.get("t", "")[-8:]  # HH:MM:SS
            src = entry.get("f", "?")
            msg = entry.get("m", "")
            trace = entry.get("_traceback")
            if trace:
                tb_compact = "\n".join(
                    ln[:120] for ln in trace[-3:]  # last 3 lines of traceback
                )
                parts.append(f"  [{ts}] {src} - {msg[:200]}\n    {tb_compact}")
            else:
                parts.append(f"  [{ts}] {src} - {msg[:200]}")
        if len(new_errors) > MAX_SHOWN:
            parts.append(f"  ... and {len(new_errors) - MAX_SHOWN} more")

        alert = "\n".join(parts)

        # Broadcast to recently active proxy sessions
        sessions = self.session_manager.list_sessions()
        now = datetime.now(timezone.utc)
        sent = 0
        for session in sessions:
            key = session.get("key", "")
            if not key.startswith("proxy:"):
                continue
            # Skip stale sessions (>24h idle)
            updated = session.get("updated_at")
            if updated:
                try:
                    updated_dt = datetime.fromisoformat(updated)
                    if (now - updated_dt).total_seconds() > 86400:
                        continue
                except (ValueError, TypeError):
                    pass
            # key format: proxy:<channel>:<bot>:<chat_id>
            parts_key = key.split(":", 3)
            if len(parts_key) < 4:
                continue
            proxy_key = f"{parts_key[1]}:{parts_key[2]}"
            chat_id = parts_key[3]
            try:
                await deliver_fn(
                    OutboundMessage(
                        channel=f"proxy:{proxy_key}",
                        chat_id=chat_id,
                        content=alert,
                    ),
                )
                sent += 1
            except Exception:
                logger.exception("Log check: failed to deliver to session {}", key)

        if sent:
            logger.info("Log check: alerted {} session(s)", sent)

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    async def _start_all(self) -> None:
        """Start all services and block until one exits."""
        await self.cron.start()
        await self.heartbeat.start()

        concurrency_gate: asyncio.Semaphore | None = getattr(
            self.agent, "_concurrency_gate", None
        )
        from nanobot.proxy.hub import HubTCPServer

        proxy_tcp_port = self.port + 1
        self.hub_server = HubTCPServer(
            self.config.gateway.host,
            proxy_tcp_port,
            self.agent,
            self.proxy_manager,
            concurrency_gate=concurrency_gate,
        )
        await self.hub_server.start()

        tasks = [
            self.agent.run(),
            self.proxy_manager.start_monitoring(),
            self._run_api_server(self.config.gateway.host, self.port),
        ]
        if self.open_browser_url:
            tasks.append(self._open_browser_when_ready())

        await asyncio.gather(*tasks)

    async def _run_api_server(self, host: str, api_port: int) -> None:
        """Run the settings server via uvicorn on the gateway port."""
        import uvicorn
        from nanobot.api.server import create_app as make_api_app

        webui_index = (
            Path(__file__).parent.parent.parent / "webui" / "index.html"
        ).resolve()
        api_app = make_api_app(
            webui_index, proxy_manager=self.proxy_manager
        )

        config = uvicorn.Config(
            api_app,
            host=host,
            port=api_port,
            log_level="info",
        )
        self.api_server = uvicorn.Server(config)
        # Prevent uvicorn from installing signal handlers — gateway owns lifecycle.
        self.api_server.install_signal_handlers = lambda: None
        asyncio.create_task(self.api_server.serve())
        console.print(
            f"[green]✓[/green] Settings server: http://{host}:{api_port}/"
        )

    async def _open_browser_when_ready(self) -> None:
        """Wait for the gateway to bind, then point the user's browser at the webui."""
        import webbrowser

        for _ in range(40):
            try:
                connect_host = (
                    "127.0.0.1"
                    if self.config.gateway.host in {"0.0.0.0", "::"}
                    else self.config.gateway.host
                )
                reader, writer = await asyncio.open_connection(
                    connect_host, self.port
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                break
            except OSError:
                await asyncio.sleep(0.1)
        try:
            webbrowser.open(self.open_browser_url)
            console.print(
                f"[green]✓[/green] Opened browser at {self.open_browser_url}"
            )
        except Exception as e:
            logger.warning("Could not open browser: {}", e)
            console.print(
                f"[yellow]Could not open browser ({e}); "
                f"visit {self.open_browser_url}[/yellow]"
            )

    async def _shutdown(self) -> None:
        """Graceful shutdown of all services."""
        if self.agent is not None:
            await self.agent.close_mcp()
            # Archive all sessions to history store before flush
            if self.session_manager is not None:
                for session_info in self.session_manager.list_sessions():
                    key = session_info.get("key", "")
                    try:
                        session = self.session_manager.get_or_create(key)
                        if session.messages:
                            if session.last_consolidated > 0:
                                self.agent.context.memory.raw_archive(
                                    session.messages[:session.last_consolidated]
                                )
                            unconsolidated = session.messages[session.last_consolidated:]
                            if unconsolidated:
                                await self.agent.consolidator.archive(unconsolidated)
                    except Exception:
                        logger.exception("Failed to archive session {} on shutdown", key)
            self.agent.stop()
            flushed = self.agent.sessions.flush_all()
            if flushed:
                logger.info("Shutdown: flushed {} session(s) to disk", flushed)
        if self.heartbeat is not None:
            self.heartbeat.stop()
        if self.cron is not None:
            self.cron.stop()
        if self.hub_server is not None:
            await self.hub_server.stop()
        if self.proxy_manager is not None:
            await self.proxy_manager.stop()
        if self.api_server is not None:
            self.api_server.should_exit = True
            try:
                await self.api_server.shutdown()
            except Exception:
                logger.debug("Error waiting for API server shutdown")

    # ------------------------------------------------------------------
    # Helpers (shared with CLI but kept here for self-containment)
    # ------------------------------------------------------------------

    @staticmethod
    def _migrate_cron_store(config: Config) -> None:
        """One-time migration: move legacy global cron store into the workspace."""
        from nanobot.config.paths import get_cron_dir

        legacy_path = get_cron_dir() / "jobs.json"
        new_path = config.workspace_path / "cron" / "jobs.json"
        if legacy_path.is_file() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.move(str(legacy_path), str(new_path))

    def _spawn_proxy_processes(self) -> None:
        """Spawn proxy processes for channels with a bots list."""
        extra = getattr(self.config.channels, "__pydantic_extra__", None) or {}
        model_keys = set(
            getattr(type(self.config.channels), "model_fields", {}) or {}
        )
        channel_names = set(extra.keys()) | model_keys

        spawned = 0
        spawned_channels: set[str] = set()
        for name in sorted(channel_names):
            if name.startswith("_"):
                continue
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue

            enabled = (
                section.get("enabled", False)
                if isinstance(section, dict)
                else getattr(section, "enabled", False)
            )
            if not enabled:
                continue

            bots = self._get_bots_list(section)
            if not bots:
                continue

            for bot_item in bots:
                bot_name, bot_config = self._merge_bot_config(section, bot_item)
                if bot_name:
                    self.proxy_manager.spawn(name, bot_name, bot_config)
                    spawned += 1
                    spawned_channels.add(name)

        if spawned:
            console.print(
                f"[green]✓[/green] Spawned {spawned} proxy(s) "
                f"across {len(spawned_channels)} channel(s)"
            )

    @staticmethod
    def _get_bots_list(section: Any) -> list:
        if isinstance(section, dict):
            return section.get("bots", [])
        extra = getattr(section, "__pydantic_extra__", None) or {}
        return extra.get("bots", [])

    @staticmethod
    def _merge_bot_config(section: Any, bot_item: Any) -> tuple[str, dict]:
        if isinstance(section, dict):
            base = dict(section)
        else:
            base = (
                section.model_dump()
                if hasattr(section, "model_dump")
                else dict(section)
            )
            extra = getattr(section, "__pydantic_extra__", None) or {}
            base.update(extra)

        if isinstance(bot_item, dict):
            bot_name = bot_item.get("name")
            merged = {**base, **bot_item}
        else:
            bot_name = str(bot_item)
            merged = dict(base)

        return bot_name, merged
