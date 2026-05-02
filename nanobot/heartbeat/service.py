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

    The main session agent reads the embedded goals, decides what to do
    (advance tasks, mark completed), and writes the updated state back to the DB
    via write_goal / write_event tools.

    This service is intentionally dumb: no LLM pre-judgment, no independent
    task logic.  Just a timer + bus publish with embedded goal data.
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
                sender_id="heartbeat",
                chat_id="direct",
                content=(
                    f"[Heartbeat] {now_ts}\n\n"
                    f"{goal_block}\n\n"
                    "Above are your active goals from DB.\n"
                    "- Active tasks → continue, update progress via write_goal\n"
                    "- Done → write_goal status='completed'\n"
                    "- Blocked → write_goal with blockers note\n"
                    "- No longer needed → write_goal status='archived'\n\n"
                    "Use write_goal to update status, write_event to log progress."
                ),
                media=[],
            ),
            session_key_override="cli:direct",
        )
        await self.agent_loop.bus.publish_inbound(msg)
        logger.info("Heartbeat: trigger published to main session via bus ({} goals)", len(goals))