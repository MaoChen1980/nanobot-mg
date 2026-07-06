"""Heartbeat service — skip chain, pending task check, HEARTBEAT_OK protocol."""

from __future__ import annotations

import asyncio
import json
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


class HeartbeatService:
    """Periodic heartbeat that checks tree.json for pending tasks.

    Flow
    ----
    1. Timer fires → skip chain (disabled, cooldown, session busy, no pending tasks)
    2. Build heartbeat prompt from pending tasks
    3. ``agent_loop.process_direct()`` — lightweight LLM call
    4. Check response for ``HEARTBEAT_OK`` → suppress delivery
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
        self._last_tree_mtime: float = 0.0
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

    def _tree_path(self) -> Path:
        """Return session-scoped tree.json path."""
        from nanobot.agent.context import _sanitize_session_key

        suffix = f"_{_sanitize_session_key(self.session_key)}" if self.session_key else ""
        # Use Path() constructor to avoid PurePosixPath / string creating
        # forward-slash paths on Windows which don't resolve as real paths.
        return Path(str(self.agent_loop.workspace)) / "tasks" / f"tree{suffix}.json"

    def _find_pending_tasks(self) -> list[dict]:
        """Scan tree.json for pending leaf tasks that are due (per-task cooldown check)."""
        tree_path = self._tree_path()
        if not tree_path.exists():
            return []
        try:
            raw = tree_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return []
        items: list[dict] = data.get("items", [])
        if not items:
            return []
        # Collect parent IDs so we can identify leaves
        parent_ids = {item.get("parent") for item in items if item.get("parent")}
        pending = []
        for item in items:
            if item.get("status") != "pending" or item.get("id") in parent_ids:
                continue
            # Per-task cooldown: skip if task was recently run
            task_id = item.get("id")
            interval_str = item.get("interval")
            if task_id and interval_str and self._state is not None:
                last_run = self._state.last_run(task_id)
                if last_run is not None:
                    interval_s = self._parse_interval(interval_str)
                    if last_run + interval_s > time.time():
                        # Still in cooldown window — skip
                        continue
            pending.append(item)
        return pending[:5]  # cap at 5 to keep prompt short

    def _parse_interval(self, interval_str: str) -> float:
        """Parse an interval string like '30m' or '1h' into seconds."""
        interval_str = interval_str.strip()
        if interval_str.endswith("s"):
            return float(interval_str[:-1])
        elif interval_str.endswith("m"):
            return float(interval_str[:-1]) * 60
        elif interval_str.endswith("h"):
            return float(interval_str[:-1]) * 3600
        elif interval_str.endswith("d"):
            return float(interval_str[:-1]) * 86400
        return 0.0

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

        # --- Read tree.json for pending tasks ---
        pending = self._find_pending_tasks()
        if not pending:
            logger.debug("Heartbeat skipped: no pending tasks")
            return

        # --- Skip LLM call if tree.json unchanged since last beat ---
        tree_path = self._tree_path()
        current_mtime = tree_path.stat().st_mtime if tree_path.exists() else 0
        if current_mtime <= self._last_tree_mtime:
            logger.debug("Heartbeat skipped: tree.json unchanged since last beat")
            return
        self._last_tree_mtime = current_mtime

        # --- Build prompt ---
        prompt = self._build_prompt(pending)
        logger.info("Heartbeat fire: {} pending task(s)", len(pending))

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

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(self, pending: list[dict]) -> str:
        """Build the heartbeat user message from pending tasks."""
        tree_path = self._tree_path().as_posix()
        lines = [
            "⏰ Heartbeat — pending tasks detected:",
            "",
        ]
        for item in pending:
            name = item.get("name", item.get("id", "?"))
            criteria = item.get("criteria", "")
            note = item.get("note", "")
            parts = [f"  - {name}"]
            if criteria:
                parts[0] += f" ({criteria})"
            if note:
                parts[0] += f" — {note}"
            lines.append(parts[0])
        lines.append("")
        lines.append(f"Read `{tree_path}` and decide if any task needs attention.")
        lines.append(f"- Reply {_HEARTBEAT_OK} if nothing needs to be done")
        lines.append("- If work is needed, do it (check status, update files, etc.)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_state_path(self) -> Path:
        return self.agent_loop.workspace / "tasks" / _STATE_FILE


def _is_heartbeat_ok(content: str | None) -> bool:
    """Check if LLM response is a HEARTBEAT_OK ack."""
    if not content:
        return True
    return content.strip().upper().startswith(_HEARTBEAT_OK)
