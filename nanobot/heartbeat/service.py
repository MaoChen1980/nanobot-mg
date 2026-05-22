"""Heartbeat service - periodic alarm clock for the main session."""

from __future__ import annotations

import asyncio
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

    Messages are marked ephemeral (skipped from session history) to avoid
    polluting conversation context with routine ticks.
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
            except Exception:
                logger.exception("Heartbeat error")

    async def _tick(self) -> None:
        """Fire a heartbeat trigger into the main session via the bus."""
        if not self.enabled:
            return

        from nanobot.utils.helpers import current_time_str
        now_ts = current_time_str(self.agent_loop.context.timezone)

        # Read task tree from tasks/TREE.md
        tree_path = self.agent_loop.workspace / "tasks" / "TREE.md"
        tree_content = ""
        if tree_path.exists():
            try:
                tree_content = tree_path.read_text(encoding="utf-8").strip()
            except Exception:
                logger.warning("Failed to read task tree at {}", tree_path)

        goal_block = tree_content if tree_content else "*(none — no active tasks)*"

        msg = replace(
            InboundMessage(
                channel="cli",
                sender_id=self._owner_id,
                chat_id="direct",
content=(
                    f"⏰ 定时检查 {now_ts}\n\n"
                    f"{goal_block}\n\n"
                    "**你的职责**：\n"
                    "1. 按照任务依赖关系主动推进，除非遇到 block\n"
                    "2. 有进展立即更新 TREE.md 状态\n"
                    "3. 完成后标记 completed 并总结\n"
                    "4. 遇到阻塞明确说出来，不要空等\n"
                    "5. 如果没有可推进的任务，说明原因并等待\n"
                ),
                    "4. 不要在无阻塞的任务上停下来等信号\n"
                    "5. 有进展更新 TREE.md/CURRENT.md\n"
                ),
                media=[],
                ephemeral=True,
            ),
            session_key_override="cli:direct",
        )
        await self.agent_loop.bus.publish_inbound(msg)
        logger.info("Heartbeat: trigger published to main session via bus")