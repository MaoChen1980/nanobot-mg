"""Proxy process lifecycle manager for nanobot Hub."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from typing import Any

from loguru import logger


class ProxyInfo:
    """Runtime info for a single proxy process."""
    def __init__(
        self,
        channel: str,
        bot: str,
        process: subprocess.Popen,
        registration: dict[str, Any],
        config: dict[str, Any] | None = None,
    ):
        self.channel = channel
        self.bot = bot
        self.process = process
        self.registration = registration
        self.config = config or {}
        self.last_heartbeat: float = time.time()
        self.running = True
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.bot}"

    @property
    def is_connected(self) -> bool:
        """Check if TCP connection is still alive."""
        if self.writer is None:
            return False
        try:
            return not self.writer.is_closing()
        except Exception:
            return False


class ProxyManager:
    """
    Manages lifecycle of proxy processes.

    Spawns proxy processes on startup, monitors TCP connections,
    and restarts dead proxies automatically.
    """

    def __init__(self, hub_api_base: str, proxy_tcp_port: int | None = None, config_path: str | None = None):
        self._hub_api_base = hub_api_base
        self._proxy_tcp_port = proxy_tcp_port
        self._config_path = config_path
        self._proxies: dict[str, ProxyInfo] = {}  # key -> ProxyInfo
        self._monitor_task: asyncio.Task | None = None
        self._writers: dict[int, str] = {}  # writer_id -> proxy key
        self._deliver_locks: dict[str, asyncio.Lock] = {}  # proxy_key -> Lock

    _pid_file: str | None = None

    @staticmethod
    def _set_pid_file(path: str) -> None:
        ProxyManager._pid_file = path

    @staticmethod
    def _get_pid_file() -> str:
        if ProxyManager._pid_file:
            return ProxyManager._pid_file
        workspace = os.environ.get(
            "NANOBOT_WORKSPACE",
            os.path.join(os.path.expanduser("~"), ".nanobot"),
        )
        return os.path.join(workspace, "gateway.pid")

    @staticmethod
    def _load_gateway_pid() -> int | None:
        """Read the PID file left by the previous gateway instance, if any."""
        try:
            with open(ProxyManager._get_pid_file()) as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _save_gateway_pid() -> None:
        """Write current PID so future gateways can detect orphans."""
        pid_file = ProxyManager._get_pid_file()
        try:
            os.makedirs(os.path.dirname(pid_file), exist_ok=True)
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            logger.warning("Failed to write PID file at {}", pid_file)

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        """Check if a given PID is still running."""
        import platform
        import subprocess

        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                return str(pid) in result.stdout
            else:
                os.kill(pid, 0)
                return True
        except OSError:
            return False
        except Exception:
            return False

    @staticmethod
    def _find_proxy_pids() -> list[int]:
        """Return PIDs of running proxy processes (nanobot.proxy.channels.*)."""
        import platform
        import subprocess

        pids: list[int] = []

        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH", "/V"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.strip().splitlines():
                    if "nanobot.proxy.channels" in line:
                        parts = line.split(",")
                        if len(parts) >= 2:
                            pid = parts[1].strip().strip('"')
                            if pid.isdigit():
                                pids.append(int(pid))
            else:
                # Use -o to get only PID and COMMAND columns, avoiding locale issues
                result = subprocess.run(
                    ["ps", "-eo", "pid,comm", "--no-headers"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) < 2:
                        continue
                    pid_str, comm = parts[0], parts[1]
                    if "nanobot.proxy.channels" in comm:
                        try:
                            pids.append(int(pid_str))
                        except ValueError:
                            pass
        except Exception:
            logger.warning("Failed to enumerate proxy processes")

        return pids

    @staticmethod
    def _kill_process(pid: int) -> None:
        """Kill a process by PID, cross-platform."""
        import platform
        import signal
        import subprocess

        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception as e:
            logger.warning("Failed to kill process {}: {}", pid, e)

    @staticmethod
    def cleanup_orphans() -> None:
        """Kill orphan proxy processes from a previous gateway instance.

        Safe guard: only cleans up if the old gateway process is already dead.
        If another gateway is still running, this is a no-op.
        """
        old_pid = ProxyManager._load_gateway_pid()
        if old_pid is not None and ProxyManager._pid_is_alive(old_pid):
            # Another gateway instance is still running — don't clean up
            return

        pids = ProxyManager._find_proxy_pids()
        for pid in pids:
            ProxyManager._kill_process(pid)
        if pids:
            logger.info("Cleaned up {} orphan proxy process(es)", len(pids))

    def _create_proxy_process(
        self,
        channel: str,
        bot: str,
        config: dict[str, Any] | None,
        hub_api_base: str,
        proxy_tcp_port: int,
    ) -> tuple[subprocess.Popen, io.BytesIO]:
        """Create a proxy subprocess with stdout capture.

        Shared by spawn() and _restart_proxy() so both paths
        produce identical processes.
        """
        import json
        import io
        import threading

        cmd = [
            sys.executable, "-m", f"nanobot.proxy.channels.{channel}",
            "--hub-url", hub_api_base,
            "--hub-tcp-port", str(proxy_tcp_port),
            "--channel", channel,
            "--bot", bot,
        ]
        env = dict(os.environ)
        if config:
            env["NANOBOT_PROXY_CONFIG"] = json.dumps(config)
        if self._config_path:
            env["NANOBOT_CONFIG_PATH"] = self._config_path

        process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        proxy_output = io.BytesIO()

        def capture_output():
            try:
                for line in process.stdout:
                    proxy_output.write(line)
                    logger.info("[proxy {}] {}".format(channel, line.decode().rstrip()))
            except Exception:
                logger.exception("Error capturing proxy {} output", channel)

        threading.Thread(target=capture_output, daemon=True).start()
        return process, proxy_output

    def key_for(self, channel: str, bot: str) -> str:
        return f"{channel}:{bot}"

    def spawn(
        self,
        channel: str,
        bot: str,
        config: dict[str, Any],
    ) -> subprocess.Popen:
        """Spawn a proxy process for a specific channel+bot."""
        process, proxy_output = self._create_proxy_process(
            channel, bot, config,
            self._hub_api_base, self._proxy_tcp_port or 18791,
        )
        proxy_info = ProxyInfo(
            channel=channel,
            bot=bot,
            process=process,
            registration={"channel": channel, "bot": bot},
            config=config,
        )
        proxy_info._proxy_output = proxy_output
        self._proxies[proxy_info.key] = proxy_info
        logger.info(
            "Spawned {} proxy (pid={}) for bot {}",
            channel, process.pid, bot
        )
        return process

    def register(self, registration: dict[str, Any]) -> None:
        """Record a proxy's registration info (HTTP-based, legacy)."""
        key = self.key_for(registration["channel"], registration["bot"])
        if key in self._proxies:
            self._proxies[key].registration = registration
            self._proxies[key].last_heartbeat = time.time()
            logger.debug("Proxy {} registered", key)

    def register_via_tcp(
        self,
        key: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        registration: dict[str, Any],
    ) -> bool:
        """Record a proxy's TCP connection.

        Returns True if accepted, False if rejected (stale proxy with wrong PID).

        If there's an existing connection for the same key with a DIFFERENT PID,
        the incoming connection is closed (stale proxy from a previous session).
        This prevents orphan proxies from hijacking connections from legitimate ones.

        If there's an existing connection with the SAME PID, the old connection
        is closed and replaced (same process reconnecting after a glitch).
        """
        existing = self._proxies.get(key)

        # Reject unknown proxies (not spawned by us — orphan from a previous session)
        if existing is None:
            logger.warning("Rejecting unsolicited proxy registration for {}", key)
            try:
                writer.close()
            except Exception as e:
                logger.debug("Failed to close rejected proxy writer: {}", e)
            return False

        # PID check: if we have a spawned process, only accept registration from it
        if existing.process is not None and existing.writer is not None and not existing.writer.is_closing():
            expected_pid = existing.process.pid
            actual_pid = registration.get("pid", 0)
            if actual_pid and actual_pid != expected_pid:
                logger.warning(
                    "Rejecting stale proxy {}: pid {} != expected pid {}",
                    key, actual_pid, expected_pid,
                )
                try:
                    writer.close()
                except Exception as e:
                    logger.debug("Failed to close stale proxy writer: {}", e)
                return False

        # Close old connection if any
        if existing.writer is not None:
            old_writer = existing.writer
            old_id = id(old_writer)
            if old_id in self._writers:
                del self._writers[old_id]
            try:
                if not old_writer.is_closing():
                    old_writer.close()
            except Exception as e:
                logger.debug("Failed to close old TCP connection for proxy {}: {}", key, e)
            logger.debug("Proxy {}: closed old TCP connection", key)

        self._proxies[key].reader = reader
        self._proxies[key].writer = writer
        self._proxies[key].registration = registration
        self._proxies[key].last_heartbeat = time.time()
        self._proxies[key].running = True
        writer_id = id(writer)
        self._writers[writer_id] = key
        logger.debug("Proxy {} registered via TCP", key)
        return True

    def unregister_by_writer(self, writer: asyncio.StreamWriter) -> None:
        """Remove proxy registration by writer instance.

        Only clears proxy state when *writer* is still the current writer —
        prevents a race where register_via_tcp has already updated to a new
        TCP connection while the old _handle_client's finally block is still
        running cleanup (Windows TCP half-open race).
        """
        writer_id = id(writer)
        if writer_id in self._writers:
            key = self._writers.pop(writer_id)
            if key in self._proxies:
                proxy = self._proxies[key]
                if proxy.writer is writer:
                    proxy.reader = None
                    proxy.writer = None
                    proxy.running = False
            logger.debug("Proxy {} unregistered (TCP disconnected)", key)

    def get_write_lock(self, proxy_key: str) -> asyncio.Lock:
        """Return the per-proxy write lock used by deliver_to_proxy.

        Exposed so hub's ``_handle_client`` can share the same lock and
        avoid interleaving writes from the register/error path with
        progress deliveries.
        """
        if proxy_key not in self._deliver_locks:
            self._deliver_locks[proxy_key] = asyncio.Lock()
        return self._deliver_locks[proxy_key]

    async def deliver_to_proxy(self, proxy_key: str, data: dict[str, Any]) -> bool:
        """Deliver a JSON message to a proxy via its TCP connection.

        Uses a per-proxy lock to prevent concurrent write/drain interleaving
        when multiple async tasks deliver to the same proxy (e.g. when
        message processing runs concurrently to service heartbeats).

        Returns True if the message was written successfully, False if
        the proxy is not connected or the write failed.
        """
        if proxy_key not in self._deliver_locks:
            self._deliver_locks[proxy_key] = asyncio.Lock()
        async with self._deliver_locks[proxy_key]:
            proxy = self._proxies.get(proxy_key)
            if proxy is None or proxy.writer is None or proxy.writer.is_closing():
                logger.warning("Cannot deliver to proxy {}: not connected", proxy_key)
                return False
            import json
            try:
                proxy.writer.write((json.dumps(data) + "\n").encode())
                await proxy.writer.drain()
                logger.info("Delivered to proxy {}: type={} has_media={} size={}",
                            proxy_key, data.get("type", "?"),
                            "yes" if data.get("media") else "no",
                            len(json.dumps(data)))
                return True
            except Exception as e:
                logger.error("Failed to deliver to proxy {}: {}", proxy_key, e)
                return False

    def heartbeat(self, registration: dict[str, Any]) -> None:
        """Update last heartbeat for a proxy (HTTP-based, legacy)."""
        key = self.key_for(registration["channel"], registration["bot"])
        if key in self._proxies:
            self._proxies[key].last_heartbeat = time.time()

    async def start_monitoring(self, heartbeat_timeout: float = 90.0) -> None:
        """Start background monitoring task."""
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(heartbeat_timeout)
        )

    async def stop(self) -> None:
        """Stop all proxy processes gracefully."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        proxies = list(self._proxies.items())
        if not proxies:
            return

        # Step 1: close all proxy TCP writers — this causes any proxy
        # currently inside _send_message() to get a connection error and
        # self-terminate via os._exit(1) in send_to_hub / async_send_to_hub.
        for _, proxy in proxies:
            if proxy.writer and not proxy.writer.is_closing():
                try:
                    proxy.writer.close()
                except Exception as e:
                    logger.debug("Failed to close proxy writer during stop_all: {}", e)

        # Step 2: brief concurrent window for proxies to self-exit,
        # then force-kill any that remain.
        async def _wait_or_force(key: str, proxy: ProxyInfo) -> None:
            # Give the proxy 3s to notice TCP close and exit on its own
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, proxy.process.wait),
                    timeout=3.0,
                )
                logger.info("Proxy {} exited gracefully after TCP close", key)
                return
            except asyncio.TimeoutError:
                pass
            # Process still alive — force-kill
            logger.info("Force-stopping proxy {} (pid={})", key, proxy.process.pid)
            try:
                proxy.process.terminate()
                proxy.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.process.kill()
            except Exception as e:
                logger.debug("Non-critical error during proxy {} stop", key)

        await asyncio.gather(*[_wait_or_force(k, p) for k, p in proxies])
        self._proxies.clear()

    async def _monitor_loop(self, heartbeat_timeout: float = 0.0) -> None:
        """Periodically check proxies are alive via TCP connection state.

        No heartbeat timeout needed — TCP connection liveness IS the heartbeat.
        """
        while True:
            await asyncio.sleep(15)

            # Read config file once per cycle to pick up disk changes
            disk_enabled = self._load_disk_enabled()

            for key, proxy in list(self._proxies.items()):
                ch, _ = key.split(":", 1)
                enabled = disk_enabled.get(ch, proxy.config.get("enabled", True))
                # Check if process exited
                if proxy.process.poll() is not None:
                    if proxy.running:
                        proxy.running = False
                        output = getattr(proxy, '_proxy_output', None)
                        if output:
                            output.seek(0)
                            content = output.read().decode('utf-8', errors='replace')
                            if content.strip():
                                crash_lines = content.strip().splitlines()[-50:]
                                logger.error("Proxy {} (pid={}) crashed:\n{}", key, proxy.process.pid, "\n".join(crash_lines))
                        if not enabled:
                            logger.warning("Proxy {} (pid={}) is disabled, removing", key, proxy.process.pid)
                            del self._proxies[key]
                            continue
                        await self._restart_proxy(proxy)
                # Check if TCP connection is dead (connection closed by proxy)
                elif not proxy.is_connected:
                    if not enabled:
                        logger.warning("Proxy {} is disabled, removing", key)
                        del self._proxies[key]
                        continue
                    # TCP connection lost — proxy is dead, restart
                    output = getattr(proxy, '_proxy_output', None)
                    if output:
                        output.seek(0)
                        content = output.read().decode('utf-8', errors='replace')
                        if content.strip():
                            for line in content.strip().splitlines()[-50:]:
                                logger.warning("[proxy {} TCP-disconnect] {}", key, line)
                    logger.warning(
                        "Proxy {} TCP connection lost, restarting...",
                        key
                    )
                    await self._restart_proxy(proxy)

    async def _restart_proxy(self, proxy: ProxyInfo) -> None:
        """Restart a dead proxy."""
        import platform

        # Clear stale TCP connection state for clean re-registration
        if proxy.writer is not None:
            writer_id = id(proxy.writer)
            if writer_id in self._writers:
                del self._writers[writer_id]
            try:
                if not proxy.writer.is_closing():
                    proxy.writer.close()
            except Exception as e:
                logger.debug("Failed to close stale writer during proxy restart: {}", e)
            proxy.writer = None
            proxy.reader = None
        proxy.running = False

        old_process = proxy.process
        pid = old_process.pid

        # Force-kill on Windows since terminate/wait is unreliable
        if platform.system() == "Windows":
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                )
            except Exception:
                logger.warning("Failed to force-kill old proxy {} (pid={})", proxy.key, pid, exc_info=True)
        else:
            try:
                old_process.terminate()
                old_process.wait(timeout=3)
            except Exception as e:
                logger.debug("Failed to terminate old proxy process {} (pid={}): {}", proxy.key, pid, e)

        new_process, proxy_output = self._create_proxy_process(
            proxy.channel, proxy.bot, proxy.config,
            self._hub_api_base, self._proxy_tcp_port or 18791,
        )

        proxy.process = new_process
        proxy._proxy_output = proxy_output
        proxy.running = True
        proxy.last_heartbeat = time.time()
        logger.info("Restarted proxy {} (pid={})", proxy.key, new_process.pid)

    def _load_disk_enabled(self) -> dict[str, bool]:
        """Read config file and return {channel_name: enabled} for all channels.

        Lets the monitor react to config.json changes made directly on disk
        (e.g. user toggles enabled: false while gateway is running).
        Falls back to empty dict if config file is unreadable.
        """
        if not self._config_path:
            return {}
        import json
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            channels = data.get("channels", {})
            return {
                name: ch.get("enabled", False)
                for name, ch in channels.items()
                if isinstance(ch, dict)
            }
        except FileNotFoundError:
            logger.debug("Config file not found at {}", self._config_path)
            return {}
        except Exception:
            logger.exception("Failed to read config from {}", self._config_path)
            return {}

    @property
    def proxy_count(self) -> int:
        return len(self._proxies)

    def get_proxy_keys(self) -> list[str]:
        return list(self._proxies.keys())

    def has_proxy(self, key: str) -> bool:
        """Check if a proxy with the given key is registered."""
        return key in self._proxies

    async def stop_proxy(self, key: str) -> None:
        """Stop a single proxy process and remove it from tracking."""
        proxy = self._proxies.pop(key, None)
        if proxy is None:
            return
        # Clear TCP writer state
        if proxy.writer is not None:
            writer_id = id(proxy.writer)
            if writer_id in self._writers:
                del self._writers[writer_id]
            try:
                if not proxy.writer.is_closing():
                    proxy.writer.close()
            except Exception as e:
                logger.debug("Failed to close proxy writer during force-stop: {}", e)
        # Force-kill
        import platform
        pid = proxy.process.pid
        try:
            if platform.system() == "Windows":
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                )
            else:
                proxy.process.terminate()
                proxy.process.wait(timeout=3)
        except Exception as e:
            logger.debug("Failed to kill proxy {} (pid={}): {}", key, pid, e)
        logger.info("Stopped proxy {} (pid={})", key, pid)
