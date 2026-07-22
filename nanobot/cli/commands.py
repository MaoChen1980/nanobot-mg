"""CLI commands for nanobot."""

from __future__ import annotations

import asyncio
import os
import select
import signal
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from loguru import logger

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            logger.debug("Failed to reconfigure stdout/stderr")

    # Enable Virtual Terminal Processing so Rich ANSI escape codes
    # (colours, cursor movement) render correctly instead of leaking
    # as raw text like `[36m`, `[?25l`.
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            kernel32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        logger.debug("Failed to enable Virtual Terminal Processing")

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from nanobot import __logo__, __version__


class SafeFileHistory:
    """FileHistory that sanitizes surrogate characters on write.

    Lazily imports prompt_toolkit to avoid startup cost.
    Proxies all FileHistory methods via __getattr__ for duck-typing compatibility.
    """

    def __init__(self, filename: str):
        from prompt_toolkit.history import FileHistory
        self._inner = FileHistory(filename)

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="replace").decode("utf-8")
        self._inner.store_string(safe)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
from nanobot.cli.stream import StreamRenderer, ThinkingSpinner
from nanobot.config.paths import get_workspace_path, is_default_workspace
from nanobot.config.schema import Config
from nanobot.utils.gitstore import sync_workspace_templates
from nanobot.utils.logging import logger_config
from nanobot.utils.restart import (
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)

app = typer.Typer(
    name="nanobot",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        logger.debug("Failed to flush pending TTY input")

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return

    # Windows console mode saved as ("windows_console_mode", mode_value)
    if isinstance(_SAVED_TERM_ATTRS, tuple) and _SAVED_TERM_ATTRS[0] == "windows_console_mode":
        if sys.platform == "win32":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                h = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
                if h and h != -1:
                    kernel32.SetConsoleMode(h, _SAVED_TERM_ATTRS[1])
            except Exception:
                logger.debug("Failed to restore Windows console mode")
        return

    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        logger.debug("Failed to restore terminal attributes")


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Defer heavy prompt_toolkit import to session init time (not module level)
    from prompt_toolkit import PromptSession

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.windll.kernel32
                h = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
                if h and h != -1:
                    mode = wintypes.DWORD()
                    if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                        _SAVED_TERM_ATTRS = ("windows_console_mode", mode.value)
            except Exception:
                logger.debug("Failed to save Windows console mode")

    from nanobot.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=SafeFileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=sys.stdout.isatty(),
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = _response_renderable(content, render_markdown, metadata)
    console.print()
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
    console.print(body)
    console.print()


def _response_renderable(content: str, render_markdown: bool, metadata: dict | None = None):
    """Render plain-text command output without markdown collapsing newlines."""
    if not render_markdown:
        return Text(content)
    if (metadata or {}).get("render_as") == "text":
        return Text(content)
    return Markdown(content)


def _print_info(text: str) -> None:
    """Print a compact info line (used for usage, errors, etc.)."""
    console.print(f"[dim]{text}[/dim]")


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        from prompt_toolkit import print_formatted_text
        from prompt_toolkit.formatted_text import ANSI
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    from prompt_toolkit.application import run_in_terminal
    await run_in_terminal(_write)


async def _print_interactive_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        from prompt_toolkit import print_formatted_text
        from prompt_toolkit.formatted_text import ANSI
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} nanobot[/cyan]"),
                c.print(_response_renderable(content, render_markdown, metadata)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    from prompt_toolkit.application import run_in_terminal
    await run_in_terminal(_write)


def _print_cli_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print a CLI progress line, pausing the spinner if needed."""
    if not text.strip():
        return
    with thinking.pause() if thinking else nullcontext():
        console.print(f"  [dim]↳ {text}[/dim]")


async def _print_interactive_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print an interactive progress line, pausing the spinner if needed."""
    if not text.strip():
        return
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.patch_stdout import patch_stdout
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================

onboard_app = typer.Typer(
    help="Setup nanobot, create bots for chat channels",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(onboard_app, name="onboard")


@onboard_app.callback(invoke_without_command=True)
def onboard(
    ctx: typer.Context,
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Generate default config.json and workspace."""
    if ctx.invoked_subcommand is not None:
        return
    from nanobot.config.loader import get_config_path, save_config, set_config_path
    from nanobot.config.schema import Config

    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    if config_path.exists():
        console.print(f"[green]✓[/green] Config already exists at {config_path}")
        from nanobot.config.loader import load_config
        cfg = load_config(config_path)
    else:
        cfg = Config()
        if workspace:
            cfg.agents.defaults.workspace = workspace
        save_config(cfg, config_path)
        console.print(f"[green]✓[/green] Created default config at {config_path}")

    # Create workspace
    workspace_path = workspace or cfg.agents.defaults.workspace
    ws_path = get_workspace_path(workspace_path)
    if ws_path and not ws_path.exists():
        ws_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {ws_path}")

    sync_workspace_templates(ws_path)

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print(f"  1. Start gateway: [cyan]nanobot gateway --config {config_path}[/cyan]")
    console.print("  2. Open WebUI and configure from your browser")
    console.print(
        "\n[dim]See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]"
    )


@onboard_app.command()
def feishu(
    name: str = typer.Option("feishu-bot", "--name", "-n", help="Bot name in config"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Create a Feishu bot via QR code scan and auto-configure it."""
    from nanobot.onboard.feishu import run_onboard_feishu

    run_onboard_feishu(
        bot_name=name,
        config_path=config,
        print_fn=lambda s: console.print(s),
    )


@onboard_app.command()
def dingtalk(
    name: str = typer.Option("dingtalk-bot", "--name", "-n", help="Bot name in config"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Create a DingTalk bot via QR code scan and auto-configure it."""
    from nanobot.onboard.dingtalk import run_onboard_dingtalk

    run_onboard_dingtalk(
        bot_name=name,
        config_path=config,
        print_fn=lambda s: console.print(s),
    )


@app.command()
def init(
    project_dir: str = typer.Argument(".", help="Project directory to scan"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to nanobot config"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-scan even if project_card.md already exists"),
):
    """Scan a project and generate project_card.md for coding agent use.

    Reads the actual filesystem (not docs, not training data) and produces
    a structured project card. The agent reads this to understand the project
    instead of guessing from training-data knowledge.
    """
    from nanobot.agent.project_scanner import write_project_card
    from pathlib import Path

    target = Path(project_dir).expanduser().resolve()
    if not target.is_dir():
        console.print(f"[red]Error: directory not found: {target}[/red]")
        raise typer.Exit(1)

    card_path = target / "project_card.md"
    if card_path.exists() and not force:
        console.print(f"[green]✓[/green] project_card.md already exists at {card_path}")
        console.print("  Use [cyan]--force[/cyan] to re-scan.")
    else:
        console.print(f"[dim]Scanning project: {target}[/dim]")
        try:
            write_project_card(target)
            console.print(f"[green]✓[/green] Generated [bold]{card_path}[/bold]")
        except Exception as e:
            console.print(f"[red]Error scanning project: {e}[/red]")
            raise typer.Exit(1)

    tasks_dir = target / "tasks"
    if not tasks_dir.is_dir():
        tasks_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created [bold]{tasks_dir}[/bold]")

    tree_path = target / "tasks" / "tree.json"
    if not tree_path.exists():
        tree_path.write_text('{"schema_version":1,"items":[]}', encoding="utf-8")
        console.print(f"[green]✓[/green] Created [bold]{tree_path}[/bold]")

    console.print("\n[bold]Project initialized for coding agent.[/bold]")
    console.print("  Next: start the agent with [cyan]nanobot agent --project-root .[/cyan]")


def _make_provider(config: Config):
    """Create the appropriate LLM provider from config by delegating to providers.factory."""
    from nanobot.providers.factory import make_provider

    try:
        return make_provider(config)
    except ValueError as exc:
        logger.error("Failed to create provider: {}", exc)
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from nanobot.config.loader import load_config, resolve_config_env_vars, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            logger.error("Config file not found: {}", config_path)
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    try:
        loaded = resolve_config_env_vars(load_config(config_path))
    except ValueError as e:
        logger.error("Config loading error: {}", e)
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    _warn_deprecated_config_keys(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace

    # Configure logging
    logger_config.configure(loaded.logging)

    return loaded


def _warn_deprecated_config_keys(config_path: Path | None) -> None:
    """Hint users to remove obsolete keys from their config file."""
    import json

    from nanobot.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to read config at {}", path, exc_info=True)
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        console.print(
            "[dim]Hint: `memoryWindow` in your config is no longer used "
            "and can be safely removed.[/dim]"
        )


# ============================================================================
# OpenAI-Compatible API Server
# ============================================================================


def _migrate_cron_store(config: "Config") -> None:
    """One-time migration: move legacy global cron store into the workspace."""
    from nanobot.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))






# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the nanobot gateway."""
    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)
    cfg = _load_runtime_config(config, workspace)
    browser_host = "127.0.0.1" if cfg.gateway.host in {"0.0.0.0", "::"} else cfg.gateway.host
    _run_gateway(cfg, port=port, open_browser_url=f"http://{browser_host}:{port or cfg.gateway.port}")


def _run_gateway(
    config: Config,
    *,
    port: int | None = None,
    open_browser_url: str | None = None,
) -> None:
    """Shared gateway runtime; ``open_browser_url`` opens a tab once channels are up."""
    from nanobot.gateway.app import GatewayApplication

    app = GatewayApplication(config, port=port, open_browser_url=open_browser_url)
    app.run()


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    project_root: str | None = typer.Option(None, "--project-root", "-p", help="Project root directory (enables coding agent mode with project card)"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode (save raw prompts to ~/.nanobot/debug/)"),
):
    """Interact with the agent directly."""
    from pathlib import Path

    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.cron.service import CronService

    if debug:
        from nanobot.agent.context_vars import _current_debug_enabled
        _current_debug_enabled.set(True)

    config = _load_runtime_config(config, workspace)
    project_root_path = Path(project_root).expanduser().resolve() if project_root else None
    sync_workspace_templates(config.workspace_path)

    # CLI mode: show tool call hints (like Feishu channel does)
    config.channels.send_tool_hints = True

    bus = MessageBus()
    provider = _make_provider(config)
    from nanobot.agent.llm_context import set_llm
    set_llm(provider, config.agents.defaults.model)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        project_root=project_root_path,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        disabled_skills=config.agents.defaults.disabled_skills,
        tools_config=config.tools,
        pt_save_interval=config.agents.defaults.extractor.save_interval,
        assess_interval=config.agents.defaults.assess_interval,
    )
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        _print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    async def _cli_progress(content: str, *, tool_hint: bool = False, **_kwargs: Any) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, _thinking)

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            renderer = StreamRenderer(render_markdown=markdown)
            response = await agent_loop.process_direct(
                message, session_id,
                on_progress=_cli_progress,
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                _print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                )
            if response and response.usage:
                usage = response.usage
                parts = []
                if usage.get("prompt_tokens"):
                    parts.append(f"↑{usage['prompt_tokens']}")
                if usage.get("completion_tokens"):
                    parts.append(f"↓{usage['completion_tokens']}")
                if usage.get("cached_tokens"):
                    parts.append(f"⚡{usage['cached_tokens']}")
                if parts:
                    _print_info(f"tokens: {' '.join(parts)}")
            if response and response.error:
                _print_info(f"error: {response.error[:200]}")
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from nanobot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode [bold blue]({config.agents.defaults.model})[/bold blue] — type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        # Wire cron handler for interactive mode
        async def _cli_cron_handler(job: Any) -> str | None:
            from nanobot.agent.tools.cron import CronTool
            cron = agent_loop.tools.get("cron")
            cron_token = None
            cron_job_token = None
            if isinstance(cron, CronTool):
                cron_token = cron.set_cron_context(True)
                cron_job_token = cron.set_current_job_id(job.id)

            # Check dispatch policy
            policy = getattr(job.payload, "policy", "queue")
            if policy not in ("queue", "idle", "interrupt"):
                policy = "queue"

            target_channel = job.payload.channel or cli_channel
            target_chat_id = job.payload.to or cli_chat_id
            target_session = job.payload.session_key or f"{target_channel}:{target_chat_id}"

            reminder_note = (
                "The scheduled time has arrived. Deliver this reminder to the user now, "
                "as a brief and natural message in their language. Speak directly to them — "
                "do not narrate progress, summarize, include user IDs, or add status reports "
                "like 'Done' or 'Reminded'.\n\n"
                f"Reminder: {job.payload.message}\n\n"
                "You can use `cron` tool to manage this job:\n"
                f"- `cron action=update job_id={job.id} message=\"...\"` — update the reminder for next run\n"
                f"- `cron action=list` — check job status\n"
                f"- `cron action=remove job_id={job.id}` — cancel this job"
            )

            # "idle" policy: skip if session is busy
            if policy == "idle" and agent_loop.is_session_busy(target_session):
                console.print(f"[dim]⏰ Cron: job '{job.name}' skipped (idle policy, session busy)[/dim]")
                if isinstance(cron, CronTool) and cron_token is not None:
                    cron.reset_cron_context(cron_token)
                return None

            # "interrupt" policy: cancel current tasks
            if policy == "interrupt":
                cancelled = await agent_loop.cancel_session_tasks(target_session)
                if cancelled > 0:
                    console.print(f"[dim]⏰ Cron: interrupted {cancelled} active task(s)[/dim]")

            try:
                resp = await agent_loop.process_direct(
                    reminder_note,
                    session_key=f"cron:{job.id}",
                    channel=target_channel,
                    chat_id=target_chat_id,
                )
            finally:
                if isinstance(cron, CronTool) and cron_token is not None:
                    cron.reset_cron_context(cron_token)
                if isinstance(cron, CronTool) and cron_job_token is not None:
                    cron.reset_current_job_id(cron_job_token)
            response = resp.content if resp else ""
            if response:
                console.print(f"\n[bold yellow]⏰ Cron:[/bold yellow] {response}\n")
            return response

        cron.on_job = _cli_cron_handler

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            await cron.start()
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, dict]] = []
            stream_buf = ""

            async def _consume_outbound():
                nonlocal stream_buf
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                        if msg.metadata.get("_stream_delta"):
                            stream_buf += msg.content
                            continue
                        if msg.metadata.get("_stream_end"):
                            continue
                        if msg.metadata.get("_streamed"):
                            turn_done.set()
                            if msg.content:
                                await _print_interactive_response(
                                    msg.content,
                                    render_markdown=markdown,
                                    metadata=msg.metadata,
                                )
                            continue

                        if msg.metadata.get("_progress") or msg.metadata.get("_retry_wait"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_progress_line(msg.content, _thinking)
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append((msg.content, dict(msg.metadata or {})))
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        stream_buf = ""

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                            metadata={"_wants_stream": True},
                        ))

                        # Poll with 1s timeout so the event loop checks signals (Windows IOCP)
                        while True:
                            try:
                                await asyncio.wait_for(turn_done.wait(), timeout=1.0)
                                break
                            except asyncio.TimeoutError:
                                pass

                        if turn_response:
                            content, meta = turn_response[0]
                            _print_agent_response(
                                content, render_markdown=markdown, metadata=meta,
                            )
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                cron.stop()
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show channel status."""
    from nanobot.proxy.registry import discover_all
    from nanobot.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled")

    for name, info in sorted(discover_all().items()):
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            info["display_name"],
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. weixin, whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with a channel via QR code or other interactive login."""
    from nanobot.proxy.registry import discover_all
    from nanobot.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)
    getattr(config.channels, channel_name, None) or {}

    # Validate channel exists
    all_channels = discover_all()
    if channel_name not in all_channels:
        available = ", ".join(all_channels.keys())
        logger.error("Unknown channel: {}  Available: {}", channel_name, available)
        console.print(f"[red]Unknown channel: {channel_name}[/red]  Available: {available}")
        raise typer.Exit(1)

    info = all_channels[channel_name]
    console.print(f"{__logo__} {info['display_name']} channel\n")
    console.print("Proxy channels use config-based authentication (edit config.json to configure credentials).")
    console.print("Use 'nanobot channels list' to see available channels.")


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list():
    """List all discovered channels (built-in and plugins)."""
    from nanobot.proxy.registry import discover_all, discover_channel_names
    from nanobot.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled")

    for name in sorted(all_channels):
        info = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            info["display_name"],
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from nanobot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        logger.error("Unknown OAuth provider: {}  Supported: {}", provider, names)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        logger.error("Login not implemented for {}", spec.label)
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            logger.exception("Failed to retrieve saved OAuth token")
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            logger.error("Authentication failed for OpenAI Codex")
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        logger.warning("oauth_cli_kit not installed")
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    try:
        from nanobot.providers.github_copilot_provider import login_github_copilot

        console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
        token = login_github_copilot(
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
        account = token.account_id or "GitHub"
        console.print(f"[green]✓ Authenticated with GitHub Copilot[/green]  [dim]{account}[/dim]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
