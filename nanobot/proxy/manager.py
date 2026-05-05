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

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.bot}"


class ProxyManager:
    """
    Manages lifecycle of proxy processes.

    Spawns proxy processes on startup, monitors heartbeats,
    and restarts dead proxies automatically.
    """

    def __init__(self, hub_api_base: str):
        self._hub_api_base = hub_api_base
        self._proxies: dict[str, ProxyInfo] = {}  # key -> ProxyInfo
        self._monitor_task: asyncio.Task | None = None

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
        # Build the proxy entrypoint
        proxy_module = f"nanobot.proxy.channels.{channel}"

        cmd = [
            sys.executable, "-m", proxy_module,
            "--hub-url", self._hub_api_base,
            "--channel", channel,
            "--bot", bot,
        ]

        # Pass channel config via env for security (no CLI args for secrets)
        import json
        env = dict(os.environ)
        env["NANOBOT_PROXY_CONFIG"] = json.dumps(config)

        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Capture proxy output for crash diagnostics
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
        """Record a proxy's registration info (called after proxy calls /api/register)."""
        key = self.key_for(registration["channel"], registration["bot"])
        if key in self._proxies:
            self._proxies[key].registration = registration
            self._proxies[key].last_heartbeat = time.time()
            logger.debug("Proxy {} registered", key)

    def heartbeat(self, registration: dict[str, Any]) -> None:
        """Update last heartbeat for a proxy."""
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

    async def _monitor_loop(self, heartbeat_timeout: float) -> None:
        """Periodically check proxies are alive and restart dead ones."""
        while True:
            await asyncio.sleep(15)
            now = time.time()
            for key, proxy in list(self._proxies.items()):
                if proxy.process.poll() is not None:
                    # Process has exited
                    if proxy.running:
                        # Log captured proxy output for diagnostics
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
                elif heartbeat_timeout > 0 and now - proxy.last_heartbeat > heartbeat_timeout:
                    # No heartbeat, assume dead - log proxy output for diagnostics
                    output = getattr(proxy, '_proxy_output', None)
                    if output:
                        output.seek(0)
                        content = output.read().decode('utf-8', errors='replace')
                        if content.strip():
                            for line in content.strip().splitlines()[-50:]:
                                logger.warning("[proxy {} heartbeat-timeout] {}", key, line)
                    logger.warning(
                        "Proxy {} missed heartbeat, restarting...",
                        key
                    )
                    self._restart_proxy(proxy)

    def _restart_proxy(self, proxy: ProxyInfo) -> None:
        """Restart a dead proxy."""
        import platform
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

        new_process = subprocess.Popen(cmd, env=env)
        proxy.process = new_process
        proxy.running = True
        proxy.last_heartbeat = time.time()
        logger.info("Restarted proxy {} (pid={})", proxy.key, new_process.pid)

    @property
    def proxy_count(self) -> int:
        return len(self._proxies)

    def get_proxy_keys(self) -> list[str]:
        return list(self._proxies.keys())