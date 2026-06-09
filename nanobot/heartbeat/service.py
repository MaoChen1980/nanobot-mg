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
                    f"⏰ Heartbeat {now_ts}\n\n"
                    f"任务树：\n{goal_block}\n\n"
                    "记住原始任务目标，所有决策围绕最终交付。\n\n"
                    "**行动原则（不要等用户指令，除非真的 block 了）**：\n\n"
                    "1. 看 workspace/tasks/TREE.md 中任务的依赖关系，优先推进没有前置依赖的\n"
                    "2. 前置依赖已满足的 task → 立即开始\n"
                    "3. 有进展 → 更新 workspace/tasks/CURRENT.md 和 workspace/tasks/TREE.md\n"
                    "4. 真正 block（缺信息/缺决策/缺资源）→ 明确说明卡在哪、需要什么\n"
                    "5. task 全部完成 → 标记 completed\n"
                    "6. 暂停的 task (## paused) 不要主动恢复，除非用户明确要求\n\n"
                    "**不要**：停下来等用户说「继续」或「做什么」\n"
                    "**要**：持续推进任务树，除非遇到上述 block 情况\n"
                ),
                media=[],
                ephemeral=True,
            ),
            session_key_override="cli:direct",
        )
        await self.agent_loop.bus.publish_inbound(msg)
        logger.info("Heartbeat: trigger published to main session via bus")