"""Heartbeat service - periodic alarm clock for the main session."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


class HeartbeatService:
    """
    Periodic alarm clock that injects active goals (from DB) into the main
    session via the message bus.

    Uses the owner's sender_id so the LLM treats these as user commands,
    not system notifications.
    """

    def __init__(
        self,
        agent_loop: "AgentLoop",
        interval_s: int = 30 * 60,
        enabled: bool = True,
        owner_id: str = "boss",
    ):
        self.agent_loop = agent_loop
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None
        self._owner_id = owner_id  # User's sender_id to impersonate (default: boss)

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

        now_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")

        # Read active goals from DB
        goals = self.agent_loop._db.list_goals(status="in_progress")

        # Build message with goal list
        if goals:
            lines = ["## Active Tasks\n"]
            for g in goals:
                subtasks_str = ""
                if g.get("subtasks"):
                    todo = [s for s in g["subtasks"] if s.get("status") == "todo"]
                    done = [s for s in g["subtasks"] if s.get("status") == "done"]
                    if todo:
                        subtasks_str = f" [{len(done)}/{len(todo) + len(done)} done]"
                lines.append(f"- **{g['title']}**{subtasks_str} [{g.get('status', 'in_progress')}] [{g.get('id', '')}]")
            goal_block = "\n".join(lines)
        else:
            goal_block = "*(none — no active goals)*"

        msg = replace(
            InboundMessage(
                channel="cli",
                sender_id=self._owner_id,
                chat_id="direct",
                content=(
                    f"定时检查 {now_ts}\n\n"
                    f"{goal_block}\n\n"
                    "有进展就更新一下状态，别忘了记录里程碑。\n"
                    "有问题就说，阻塞太久不好。\n"
                    "完成的就标记 completed。\n"
                ),
                media=[],
            ),
            session_key_override="cli:direct",
        )
        await self.agent_loop.bus.publish_inbound(msg)
        logger.info("Heartbeat: trigger published to main session via bus ({} goals)", len(goals))