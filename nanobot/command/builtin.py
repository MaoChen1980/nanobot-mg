"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from nanobot import __version__
from nanobot.bus.events import OutboundMessage
from nanobot.agent.subagent_status import SubagentStatus
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.utils.helpers import build_status_content
from nanobot.utils.restart import set_restart_notice_to_env


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(msg.session_key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg
    set_restart_notice_to_env(
        channel=msg.channel,
        chat_id=msg.chat_id,
        metadata=dict(msg.metadata or {}),
    )

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "nanobot"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    try:
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    except Exception:
        pass
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

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
        pass  # Never let usage fetch break /status
    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    try:
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    except Exception:
        pass
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    msg = ctx.msg
    cancelled = await loop._cancel_active_tasks(ctx.key)

    # Archive unconsolidated messages in background
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot))

    stopped = f"Stopped {cancelled} running task(s)." if cancelled else "No running tasks."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=f"New session started. {stopped}",
        metadata=dict(msg.metadata or {})
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        t0 = time.monotonic()
        try:
            did_work = await loop.dream.run()
            elapsed = time.monotonic() - t0
            if did_work:
                content = f"Dream completed in {elapsed:.1f}s."
            else:
                content = "Dream: nothing to process."
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest commit (HEAD~1 vs HEAD).
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle."
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest commit's diff
        commits = git.log(max_entries=1)
        result = git.show_commit_diff(commits[0].sha) if commits else None
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = "Dream memory has no saved versions yet."

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent commits for the user to pick
        commits = git.log(max_entries=10)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        changed_files = _format_changed_files(result[1]) if result else "the tracked memory files"
        new_sha = git.revert(sha)
        if new_sha:
            content = (
                f"Restored Dream memory to the state before `{sha}`.\n\n"
                f"- New safety commit: `{new_sha}`\n"
                f"- Restored files: {changed_files}\n\n"
                f"Use `/dream-log {new_sha}` to inspect the restore diff."
            )
        else:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "It may not exist, or it may be the first saved version with no earlier state to restore."
            )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = [
        "🐈 nanobot commands:",
        "/new /clear /reset — Stop current task and start a new conversation",
        "/stop — Stop the current task",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/dream — Manually trigger Dream consolidation",
        "/dream-log — Show what the last Dream changed",
        "/dream-restore — Revert memory to a previous state",
        "/sub — Show subagent status",
        "/help — Show available commands",
    ]
    return "\n".join(lines)


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
            lines.append(f"   ⚠️ task not in running dict (may be done)")
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
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


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
            lines.append(f"   ⚠️ task not in running dict (may be done)")
    return "\n".join(lines)


async def cmd_goal(ctx: CommandContext) -> OutboundMessage:
    """Execute a goal by ID: /goal <goal_id>"""
    loop = ctx.loop
    msg = ctx.msg
    args = ctx.args.strip()

    if not args:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="Usage: /goal <goal_id>\nUse /list_goals to see available goals.",
            metadata=dict(msg.metadata or {}),
        )

    goal_id = args.split()[0]

    # Check if TaskExecutor is available
    if not hasattr(loop, "_task_executor"):
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="TaskExecutor not initialized. Goal execution unavailable.",
            metadata=dict(msg.metadata or {}),
        )

    # Get goal from DB
    db = loop._db
    if db is None:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="Database not available.",
            metadata=dict(msg.metadata or {}),
        )

    goal = db.get_goal(goal_id)
    if not goal:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"Goal '{goal_id}' not found. Use /list_goals to see available goals.",
            metadata=dict(msg.metadata or {}),
        )

    # Execute goal via TaskExecutor
    result = await loop._task_executor.execute_goal(
        goal_id=goal_id,
        goal=goal,
        session_key=ctx.key,
        context_window_tokens=loop.context_window_tokens,
        context_block_limit=loop.context_block_limit,
        provider_retry_mode=loop.provider_retry_mode,
    )

    # Format response
    content = f"Goal '{goal_id}' execution result: {result.status}"
    if result.message:
        content += f"\n{result.message}"

    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=content,
        metadata={**dict(msg.metadata or {}), "goal_id": goal_id, "goal_status": result.status},
    )


async def cmd_resume_goal(ctx: CommandContext) -> OutboundMessage:
    """Resume a blocked or paused goal: /resume_goal <goal_id>"""
    loop = ctx.loop
    msg = ctx.msg
    args = ctx.args.strip()

    if not args:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="Usage: /resume_goal <goal_id>",
            metadata=dict(msg.metadata or {}),
        )

    goal_id = args.split()[0]

    # Check if TaskExecutor is available
    if not hasattr(loop, "_task_executor"):
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="TaskExecutor not initialized. Goal execution unavailable.",
            metadata=dict(msg.metadata or {}),
        )

    # Resume goal via TaskExecutor
    result = await loop._task_executor.resume_goal(
        goal_id=goal_id,
        session_key=ctx.key,
        context_window_tokens=loop.context_window_tokens,
        context_block_limit=loop.context_block_limit,
        provider_retry_mode=loop.provider_retry_mode,
    )

    # Format response
    content = f"Goal '{goal_id}' resume result: {result.status}"
    if result.message:
        content += f"\n{result.message}"

    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=content,
        metadata={**dict(msg.metadata or {}), "goal_id": goal_id, "goal_status": result.status},
    )


async def cmd_list_goals(ctx: CommandContext) -> OutboundMessage:
    """List goals with optional filters: /list_goals [--project=<project>] [--scope=<scope>] [--status=<status>]"""
    loop = ctx.loop
    msg = ctx.msg

    # Parse arguments
    args = ctx.args.strip()
    project = None
    scope = None
    status = None

    for part in args.split():
        if part.startswith("--project="):
            project = part.split("=", 1)[1]
        elif part.startswith("--scope="):
            scope = part.split("=", 1)[1]
        elif part.startswith("--status="):
            status = part.split("=", 1)[1]

    # Get DB
    db = loop._db
    if db is None:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="Database not available.",
            metadata=dict(msg.metadata or {}),
        )

    # Query goals
    goals = db.list_goals(status=status, project=project, scope=scope)

    if not goals:
        filters = []
        if project:
            filters.append(f"project={project}")
        if scope:
            filters.append(f"scope={scope}")
        if status:
            filters.append(f"status={status}")
        filter_str = f" ({', '.join(filters)})" if filters else ""
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"No goals found{filter_str}.",
            metadata=dict(msg.metadata or {}),
        )

    # Format output
    lines = [f"**Goals** ({len(goals)} found)"]
    for g in goals[:20]:  # Limit to 20
        status_emoji = {"in_progress": "🔄", "completed": "✅", "paused": "⏸️", "blocked": "🚫", "archived": "📦"}.get(g.get("status", ""), "❓")
        title = g.get("title", "Untitled")[:40]
        goal_id = g.get("id", "?")
        project_name = g.get("project", "-")
        scopes = g.get("data", {}).get("scopes", [])
        scopes_str = f" [{', '.join(scopes)}]" if scopes else ""
        lines.append(f"{status_emoji} [{goal_id}] {title}")
        lines.append(f"   project={project_name}{scopes_str}")

    if len(goals) > 20:
        lines.append(f"... and {len(goals) - 20} more")

    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content="\n".join(lines),
        metadata={**dict(msg.metadata or {}), "render_as": "text"},
    )


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/goal", cmd_goal)
    router.exact("/resume_goal", cmd_resume_goal)
    router.exact("/list_goals", cmd_list_goals)
    router.exact("/new", cmd_new)
    router.exact("/clear", cmd_new)
    router.exact("/reset", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
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
