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

    def __init__(self, hub_api_base: str, proxy_tcp_port: int | None = None):
        self._hub_api_base = hub_api_base
        self._proxy_tcp_port = proxy_tcp_port
        self._proxies: dict[str, ProxyInfo] = {}  # key -> ProxyInfo
        self._monitor_task: asyncio.Task | None = None
        self._writers: dict[int, str] = {}  # writer_id -> proxy key

    def key_for(self, channel: str, bot: str) -> str:
        return f"{channel}:{bot}"

    def spawn(
        self,
        channel: str,
        bot: str,
        config: dict[str, Any],
    ) -> subprocess.Popen:
        """
        Spawn a proxy process for a specific channel+bot.

        Args:
            channel: channel name (e.g. "feishu")
            bot: bot name (e.g. "nanobot")
            config: channel config dict from config.json

        Returns:
            Popen handle to the spawned process
        """
        proxy_module = f"nanobot.proxy.channels.{channel}"

        cmd = [
            sys.executable, "-m", proxy_module,
            "--hub-url", self._hub_api_base,
            "--hub-tcp-port", str(self._proxy_tcp_port or 18791),
            "--channel", channel,
            "--bot", bot,
        ]

        import json
        env = dict(os.environ)
        env["NANOBOT_PROXY_CONFIG"] = json.dumps(config)

        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        import io
        proxy_output = io.BytesIO()
        def capture_output():
            try:
                for line in process.stdout:
                    proxy_output.write(line)
                    logger.debug("[proxy {}] {}".format(channel, line.decode().rstrip()))
            except Exception:
                pass
        import threading
        threading.Thread(target=capture_output, daemon=True).start()

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
            except Exception:
                pass
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
                except Exception:
                    pass
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
            except Exception:
                pass
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
        """Remove proxy registration by writer instance."""
        writer_id = id(writer)
        if writer_id in self._writers:
            key = self._writers.pop(writer_id)
            if key in self._proxies:
                self._proxies[key].reader = None
                self._proxies[key].writer = None
                self._proxies[key].running = False
            logger.debug("Proxy {} unregistered (TCP disconnected)", key)

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

        for key, proxy in list(self._proxies.items()):
            logger.info("Stopping proxy {} (pid={})", key, proxy.process.pid)
            try:
                proxy.process.terminate()
                proxy.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.process.kill()
            except Exception as e:
                logger.warning("Error stopping proxy {}: {}", key, e)

        self._proxies.clear()

    async def _monitor_loop(self, heartbeat_timeout: float = 0.0) -> None:
        """Periodically check proxies are alive via TCP connection state.

        No heartbeat timeout needed — TCP connection liveness IS the heartbeat.
        """
        while True:
            await asyncio.sleep(15)
            for key, proxy in list(self._proxies.items()):
                # Check if process exited
                if proxy.process.poll() is not None:
                    if proxy.running:
                        output = getattr(proxy, '_proxy_output', None)
                        if output:
                            output.seek(0)
                            content = output.read().decode('utf-8', errors='replace')
                            if content.strip():
                                for line in content.strip().splitlines()[-50:]:
                                    logger.warning("[proxy {} crash] {}", key, line)
                        logger.warning(
                            "Proxy {} (pid={}) died unexpectedly, restarting...",
                            key, proxy.process.pid
                        )
                        proxy.running = False
                        self._restart_proxy(proxy)
                # Check if TCP connection is dead (connection closed by proxy)
                elif not proxy.is_connected:
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
                    self._restart_proxy(proxy)

    def _restart_proxy(self, proxy: ProxyInfo) -> None:
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
            except Exception:
                pass
            proxy.writer = None
            proxy.reader = None
        proxy.running = False

        old_process = proxy.process
        pid = old_process.pid

        # Force-kill on Windows since terminate/wait is unreliable
        if platform.system() == "Windows":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                )
            except Exception:
                pass
        else:
            try:
                old_process.terminate()
                old_process.wait(timeout=3)
            except Exception:
                pass

        cmd = [
            sys.executable, "-m", f"nanobot.proxy.channels.{proxy.channel}",
            "--hub-url", self._hub_api_base,
            "--hub-tcp-port", str(self._proxy_tcp_port or 18791),
            "--channel", proxy.channel,
            "--bot", proxy.bot,
        ]
        env = dict(os.environ)
        if proxy.config:
            import json
            env["NANOBOT_PROXY_CONFIG"] = json.dumps(proxy.config)
            logger.debug("Restart {} with config: appId={}", proxy.key, proxy.config.get("appId"))
        else:
            logger.warning("Restart {} with EMPTY config!", proxy.key)

        import io
        new_process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        proxy_output = io.BytesIO()

        def capture_output():
            try:
                for line in new_process.stdout:
                    proxy_output.write(line)
                    logger.debug("[proxy {}] {}".format(proxy.channel, line.decode().rstrip()))
            except Exception:
                pass

        import threading
        threading.Thread(target=capture_output, daemon=True).start()

        proxy.process = new_process
        proxy._proxy_output = proxy_output
        proxy.running = True
        proxy.last_heartbeat = time.time()
        logger.info("Restarted proxy {} (pid={})", proxy.key, new_process.pid)

    @property
    def proxy_count(self) -> int:
        return len(self._proxies)

    def get_proxy_keys(self) -> list[str]:
        return list(self._proxies.keys())