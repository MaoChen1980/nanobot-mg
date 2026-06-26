"""
SelfCleanupHook: clean up tmp files created during this session.

Phase 3 (final) of the self-evolution feedback loop. Acts as a safety net
when the agent forgets to clean up tmp files — this hook runs at session end
and removes common tmp artifacts.

Unlike ContextInjectHook (Phase 0) and SelfDetectHook (Phase 2), this is a
pure cleanup hook with no LLM dependency.
"""
from __future__ import annotations

import time
from pathlib import Path

from nanobot.agent.hook import AgentHook, AgentRunHookContext


class SelfCleanupHook(AgentHook):
    """Remove tmp files created during the session as a safety net.

    Cleans up common patterns: *.py, *.bat, *.txt, *.sh in workspace/tmp/
    that were created during this session (modified in the last 30 minutes).
    """

    def __init__(self) -> None:
        super().__init__()
        self._session_start: float = time.time()

    async def after_run(self, context: AgentRunHookContext) -> None:
        """Delete tmp files created during this session."""
        # workspace/tmp is at ~/.nanobot/workspace/tmp
        tmp_dir = Path.home() / ".nanobot" / "workspace" / "tmp"
        if not tmp_dir.exists():
            return

        max_age_seconds = 30 * 60  # 30 minutes — assume created in this session
        cutoff = self._session_start - max_age_seconds
        removed = 0

        for path in tmp_dir.iterdir():
            if not path.is_file():
                continue
            # Only delete known tmp patterns
            if path.suffix in (".py", ".bat", ".sh", ".txt"):
                try:
                    mtime = path.stat().st_mtime
                    if mtime >= cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    pass

        # Log is not available in after_run, so silently clean up
