"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from nanobot import __version__
from nanobot.bus.events import OutboundMessage
from nanobot.agent.subagent_status import SubagentStatus
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.utils.helpers import build_status_content
from nanobot.utils.restart import write_restart_notice_env_vars


async def cmd_stop(ctx: CommandContext) -> OutboundMessage | None:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop.cancel_active_tasks(msg.session_key)

    if total == 0 and msg.metadata.get("_stop_redispatch"):
        # Re-dispatched /stop after cancellation — let it fall through
        # to LLM so it can update tree.json (active → paused) and confirm.
        return None

    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Write restart flag so the gateway restart loop re-invokes the process."""
    msg = ctx.msg
    write_restart_notice_env_vars(
        channel=msg.channel,
        chat_id=msg.chat_id,
        metadata=dict(msg.metadata or {}),
    )

    # Write the same flag file that self_restart tool uses — the gateway
    # restart loop detects it, calls _shutdown(), and re-invokes _async_run().
    from pathlib import Path
    flag_file = Path.home() / ".nanobot" / "workspace" / "_restart_flag.json"
    flag_file.parent.mkdir(parents=True, exist_ok=True)
    import json, time
    flag_file.write_text(
        json.dumps({"requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False),
        encoding="utf-8",
    )

    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = loop.last_usage.get("prompt_tokens", 0)

    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    try:
        from nanobot.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    except Exception:
        logger.debug("Failed to fetch search usage for /status")  # Never let usage fetch break /status
    active_tasks = loop.active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    try:
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    except Exception:
        logger.debug("Failed to get subagent count for /status")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop.start_time, last_usage=loop.last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.messages),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
        ),
        metadata={**dict(ctx.msg.metadata or {})},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage | None:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    msg = ctx.msg

    # Re-dispatch: first pass already cancelled tasks and cleared session.
    # Return None so the message falls through to LLM, which can update
    # tree.json (active → paused) and confirm with the user.
    if msg.metadata.get("_new_redispatch"):
        return None

    cancelled = await loop.cancel_active_tasks(ctx.key)

    session = ctx.session or loop.sessions.get_or_create(ctx.key)

    # Archive session messages to history before clearing
    if session.messages:
        try:
            loop.context.memory.condense_session_to_history(session.messages)
        except Exception:
            logger.exception("Failed to archive session to history")

    # Acquire per-session lock to prevent race with active _dispatch
    lock = loop.get_session_lock(ctx.key)
    async with lock:
        session.clear()
        loop.sessions.save(session)
        loop.sessions.invalidate(session.key)

    stopped = f"Stopped {cancelled} running task(s)." if cancelled else "No running tasks."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=f"New session started. {stopped}",
        metadata=dict(msg.metadata or {})
    )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {})},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    return """# 🐈 Nanobot Commands

## Session
- **/new /clear /reset** — Stop task & start new conversation
- **/stop** — Stop current task
- **/restart** — Restart the bot
- **/status** — Show bot status

- **/sub** — Show subagent status

## Observe
- **/think** — Show/hide LLM reasoning blocks
- **/tool** — Show/hide tool call events
- **/debug** — Save raw prompts to ~/.nanobot/debug/

## Info
- **/help** — Show this message"""


def _format_subagent_status(statuses: dict[str, "SubagentStatus"], running: dict[str, asyncio.Task[None]]) -> str:
    """Format running subagent statuses."""
    if not statuses:
        return "No active subagent."
    lines = []
    for task_id, status in sorted(statuses.items(), key=lambda x: x[1].started_at):
        is_running = task_id in running and not running[task_id].done()
        phase_emoji = {"initializing": "🔄", "awaiting_tools": "⏳", "tools_completed": "🔧", "final_response": "🧠", "done": "✅", "error": "❌"}.get(status.phase, "❓")
        elapsed = time.monotonic() - status.started_at
        lines.append(f"{phase_emoji} [{task_id}] {status.label}")
        lines.append(f"   phase={status.phase}, iter={status.iteration}, elapsed={elapsed:.0f}s")
        if status.tool_events:
            completed = len([e for e in status.tool_events if e.get("status") == "ok"])
            lines.append(f"   tools: {completed} completed / {len(status.tool_events)} total")
        if status.error:
            lines.append(f"   error: {status.error[:80]}")
        if not is_running:
            lines.append("   ⚠️ task not in running dict (may be done)")
    return "\n".join(lines)


async def cmd_sub(ctx: CommandContext) -> OutboundMessage:
    """Show status of running subagents."""
    loop = ctx.loop
    mgr = getattr(loop, "subagents", None)
    if mgr is None:
        content = "Subagent manager not available."
    elif not mgr._running_tasks and not mgr._task_statuses:
        content = "No active subagent."
    else:
        content = _format_subagent_status(mgr._task_statuses, mgr._running_tasks)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {})},
    )


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    # Observe toggles
    from nanobot.agent.commands.observe import register_observe_commands
    register_observe_commands(router)
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.priority("/new", cmd_new)
    router.priority("/clear", cmd_new)
    router.priority("/reset", cmd_new)
    router.exact("/sub", cmd_sub)
    router.exact("/help", cmd_help)

    async def cmd_unknown(ctx: CommandContext) -> OutboundMessage | None:
        raw = ctx.raw.strip()
        if raw.startswith("/") and not raw.startswith("//"):
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content=f"Unknown command: {raw.split()[0]}. Type /help for available commands.",
                metadata=dict(ctx.msg.metadata or {}),
            )
        return None
    router.intercept(cmd_unknown)
