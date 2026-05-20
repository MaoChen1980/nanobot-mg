"""
ContextMonitorHook: warns when context is bloated by writing a signal file.

The hook writes HEAVY_CONTEXT.md to alert the LLM via workspace signals.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from nanobot.agent.hook import AgentHook, AgentHookContext


class ContextMonitorHook(AgentHook):
    """Before each iteration, check context health and write a signal file."""

    # Thresholds (chars)
    HEAVY = 100_000
    CRITICAL = 200_000

    async def before_iteration(self, context: AgentHookContext) -> None:
        try:
            self._check(context)
        except Exception:
            logger.debug("ContextMonitorHook.before_iteration failed")

    def _check(self, context: AgentHookContext) -> None:
        workspace = self._resolve_workspace(context)
        if not workspace:
            return

        msg_count = len(context.messages)
        total_chars = sum(len(str(m)) for m in context.messages)

        # Find bloated messages (individual > 5000 chars)
        bloated = [
            (i, m.get("role", "?"), len(str(m)))
            for i, m in enumerate(context.messages)
            if len(str(m)) > 5000
        ]

        # Build a detailed health report
        lines = ["# Context Health Report", ""]
        lines.append(f"- Messages: {msg_count}")
        lines.append(f"- Total chars: {total_chars:,}")

        if total_chars >= self.CRITICAL:
            lines.append(f"- Status: 🔴 CRITICAL ({total_chars:,} chars)")
            lines.append("- **IMMEDIATE ACTION NEEDED**: review and compress bloated items")
        elif total_chars >= self.HEAVY:
            lines.append(f"- Status: 🟡 HEAVY ({total_chars:,} chars)")
            lines.append("- Action: review and exclude bloated items below")
        else:
            health_file = workspace / ".context_health.md"
            if health_file.exists():
                health_file.unlink()  # Clean up when context is healthy
            return

        if bloated:
            lines.append("")
            lines.append("## Bloated Messages (>5000 chars)")
            for idx, role, size in bloated[:10]:
                lines.append(f"- msg idx {idx} ({role}, {size:,} chars)")

        health_file = workspace / ".context_health.md"
        health_file.write_text("\n".join(lines), encoding="utf-8")

    def _resolve_workspace(self, ctx: AgentHookContext) -> Path | None:
        """Use ctx.workspace (injected by framework), fallback to CWD-based discovery."""
        if ctx.workspace:
            return ctx.workspace
        p = Path.cwd()
        for _ in range(6):
            if (p / "SOUL.md").exists():
                return p
            parent = p.parent
            if parent == p:
                break
            p = parent
        home_ws = Path.home() / ".nanobot" / "workspace"
        if (home_ws / "SOUL.md").exists():
            return home_ws
        return None
