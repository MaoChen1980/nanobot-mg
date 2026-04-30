"""Heartbeat service - periodic alarm clock for the main session."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


class HeartbeatService:
    """
    Periodic alarm clock that injects a trigger into the main session via
    the message bus.  The main session agent then reads HEARTBEAT.md,
    resumes any interrupted task, updates the file, and decides what to do.

    This is intentionally dumb: no LLM pre-judgment, no independent task,
    no delivery logic.  Just a timer + bus publish.
    """

    def __init__(
        self,
        agent_loop: "AgentLoop",
        interval_s: int = 30 * 60,
        enabled: bool = True,
    ):
        self.agent_loop = agent_loop
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.agent_loop.workspace / "HEARTBEAT.md"

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        """Fire a heartbeat trigger into the main session via the bus."""
        if not self.enabled:
            return

        if not self.heartbeat_file.exists():
            logger.debug("Heartbeat: HEARTBEAT.md missing")
            return

        raw = self.heartbeat_file.read_text(encoding="utf-8").strip()
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        msg = replace(
            InboundMessage(
                channel="cli",
                sender_id="heartbeat",
                chat_id="direct",
                content=(
                    f"[Heartbeat] {now}\n\n"
                    f"=== HEARTBEAT.md ===\n{raw}\n=== END ===\n\n"
                    "Above is your intermediate task state. "
                    "Continue working on active tasks and update progress. "
                    "If you discover new tasks not listed here, add them to HEARTBEAT.md "
                    "so the next heartbeat will track them. "
                    "Move completed tasks to ## Completed. "
                    "Write the latest status back to HEARTBEAT.md when done."
                ),
                media=[],
            ),
            session_key_override="cli:direct",
        )
        await self.agent_loop.bus.publish_inbound(msg)
        logger.info("Heartbeat: trigger published to main session via bus")
