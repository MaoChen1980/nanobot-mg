"""Heartbeat service — skip chain, TREE.md interval tasks, HEARTBEAT_OK protocol."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.heartbeat.state import HeartbeatState

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop

_HEARTBEAT_OK = "HEARTBEAT_OK"
_SESSION_KEY = "cli:direct"
_STATE_FILE = ".heartbeat_state.json"

_INTERVAL_RE = re.compile(
    r"^\s*[-*]\s+(.+?)\s*\[interval:\s*(\d+)\s*(s|m|h)\]\s*$",
    re.IGNORECASE,
)

_UNIT_MULT = {"s": 1, "m": 60, "h": 3600}


def _format_duration(seconds: int) -> str:
    """Format seconds to human-readable short form."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _parse_interval_tasks(tree_content: str) -> list[tuple[str, int]]:
    """Parse TREE.md for interval-annotated tasks under ``## active``.

    Returns list of ``(task_name, interval_seconds)``.
    """
    tasks: list[tuple[str, int]] = []
    in_active = False
    for line in tree_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and "active" in stripped.lower():
            in_active = True
            continue
        if stripped.startswith("## "):
            in_active = False
            continue
        if not in_active:
            continue
        m = _INTERVAL_RE.match(line)
        if m:
            name = m.group(1).strip()
            interval = int(m.group(2)) * _UNIT_MULT[m.group(3)]
            if interval > 0:
                tasks.append((name, interval))
    return tasks


def _is_heartbeat_ok(content: str | None) -> bool:
    """Check if LLM response is a HEARTBEAT_OK ack."""
    if not content:
        return True
    return content.strip().upper().startswith(_HEARTBEAT_OK)


class HeartbeatService:
    """Periodic heartbeat that checks TREE.md for due interval tasks.

    Flow
    ----
    1. Timer fires → skip chain (disabled, cooldown, session busy, no due tasks)
    2. Build heartbeat prompt from due interval tasks
    3. ``agent_loop.process_direct()`` — lightweight LLM call
    4. Check response for ``HEARTBEAT_OK`` → suppress delivery
    5. Update task run timestamps
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        interval_s: int = 1800,
        enabled: bool = True,
        min_interval_s: int = 30,
        session_key: str = _SESSION_KEY,
    ) -> None:
        self.agent_loop = agent_loop
        self.interval_s = interval_s
        self.enabled = enabled
        self.min_interval_s = min_interval_s
        self.session_key = session_key
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run: float = 0.0
        self._state: HeartbeatState | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        state_path = self._resolve_state_path()
        self._state = HeartbeatState(state_path)
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Heartbeat error")

    # ------------------------------------------------------------------
    # Tick — skip chain + prompt + LLM + response handling
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Single heartbeat beat: skip chain → prompt → LLM → handle response."""
        t0 = time.time()

        # --- Skip 1: disabled ---
        if not self.enabled:
            return

        # --- Skip 2: cooldown (min spacing) ---
        if t0 - self._last_run < self.min_interval_s:
            return

        # --- Skip 3: session busy (another dispatch in progress) ---
        if self.session_key in self.agent_loop._session_dispatch:
            logger.debug("Heartbeat skipped: session busy")
            return

        # --- Read TREE.md ---
        tree_path = self.agent_loop.workspace / "tasks" / "TREE.md"
        if not tree_path.exists():
            logger.debug("Heartbeat skipped: no TREE.md")
            return

        try:
            tree_content = tree_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Heartbeat skipped: cannot read TREE.md")
            return

        # --- Parse interval tasks ---
        interval_tasks = _parse_interval_tasks(tree_content)
        if not interval_tasks:
            logger.debug("Heartbeat skipped: no interval tasks in TREE.md")
            return

        # --- Check which tasks are due ---
        state = self._state
        if state is None:
            logger.debug("Heartbeat skipped: state not initialized")
            return
        due: list[tuple[str, int]] = []
        for name, interval_sec in interval_tasks:
            last = state.last_run(name)
            if last is None or (t0 - last) >= interval_sec:
                due.append((name, interval_sec))

        if not due:
            logger.debug("Heartbeat skipped: no interval tasks due")
            return

        # --- Build prompt ---
        prompt = self._build_prompt(due)
        logger.info("Heartbeat fire: {} task(s) due", len(due))

        # --- Send to LLM via process_direct ---
        self._last_run = t0
        try:
            response = await self.agent_loop.process_direct(
                content=prompt,
                session_key=self.session_key,
                channel="cli",
                chat_id="direct",
                ephemeral=True,
            )
        except Exception:
            logger.exception("Heartbeat: process_direct failed")
            return

        # --- Handle response ---
        if response and _is_heartbeat_ok(response.content):
            logger.debug("Heartbeat: LLM returned HEARTBEAT_OK — suppressed")
            response.content = None
        elif response:
            logger.info(
                "Heartbeat: LLM produced response ({} chars)",
                len(response.content or ""),
            )

        # --- Update task timestamps ---
        now = time.time()
        state.mark_tasks({name: now for name, _ in due})

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(self, due: list[tuple[str, int]]) -> str:
        """Build the heartbeat user message from due tasks."""
        lines = [
            "⏰ Heartbeat — due interval tasks from TREE.md:",
            "",
        ]
        for name, interval_sec in due:
            lines.append(f"  - {name} (every {_format_duration(interval_sec)})")
        lines.append("")
        lines.append("For each due task, decide if it needs attention:")
        lines.append(f"- Reply {_HEARTBEAT_OK} if nothing needs to be done")
        lines.append("- If work is needed, do it (check status, update files, etc.)")
        lines.append("- Update TREE.md with any progress made")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_state_path(self) -> Path:
        return self.agent_loop.workspace / "tasks" / _STATE_FILE
