"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import subprocess
import time
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from nanobot.agent.memory import MemoryExtractor
from nanobot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.ask import (
    AskUserTool,
    ask_user_options_from_messages,
    ask_user_outbound,
    ask_user_tool_result_messages,
    pending_ask_user_id,
)
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import DeleteFileTool, EditFileTool, ListDirTool, MoveFileTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.notebook import NotebookEditTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.self import MyTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.check_subagent import CheckSubagentTool
from nanobot.agent.tools.list_subagents import ListSubagentsTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.agent.tools.framework_search import FrameworkSearchTool
from nanobot.agent.tools.conversation_search import ConversationSearchTool
from nanobot.agent.tools.semantic_search import SearchTextTool
from nanobot.agent.tools.read_files import ReadFilesTool
from nanobot.agent.tools.explore_module import ExploreModuleTool
from nanobot.agent.tools.git_inspect import GitInspectTool
from nanobot.agent.tools.analyze_tool import AnalyzeTool
from nanobot.agent.tools.diagnose_tool import DiagnoseTool
from nanobot.agent.tools.scan_project import ScanProjectTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.agent.context_vars import _current_agent_loop, _current_debug_enabled
from nanobot.command import CommandContext, CommandRouter, register_builtin_commands
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider
from nanobot.providers.factory import ProviderSnapshot
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.document import separate_and_extract_media
from nanobot.utils.media_decode import image_placeholder_text
from nanobot.utils.helpers import truncate_text as truncate_text_fn
from nanobot.utils.progress_events import (
    build_tool_event_finish_payloads,
    build_tool_event_start_payload,
    process_tool_events_and_progress,
    on_progress_accepts_tool_events,
)
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

# Import from split modules
from .loop_constants import (
    UNIFIED_SESSION_KEY,
    _RUNTIME_CHECKPOINT_KEY,
    _PENDING_USER_TURN_KEY,
)
from .loop_utils import (
    strip_think,
    runtime_chat_id,
    tool_hint,
    cancel_active_tasks,
)
from .loop_mcp import connect_mcp as _connect_mcp, close_mcp as _close_mcp
from nanobot.agent.loop_hook import _LoopHook
from .loop_checkpoint import (
    checkpoint_message_key,
    set_runtime_checkpoint,
    clear_runtime_checkpoint,
    mark_pending_user_turn,
    clear_pending_user_turn,
    restore_and_clear_checkpoint,
    restore_pending_user_turn,
)
from .loop_checkpoint import RecoveryManager
from .loop_dispatch import DispatchManager
from .loop_message_handlers import SystemMessageHandler, UserMessageHandler

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, ToolsConfig, WebToolsConfig
    from nanobot.cron.service import CronService


@dataclasses.dataclass
class _SessionDispatchState:
    """Per-session dispatch tracking: active tasks and mid-turn injection queue."""
    tasks: list[asyncio.Task]
    pending: asyncio.Queue


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    # Expose checkpoint keys as class attrs for backward compat with tests
    _RUNTIME_CHECKPOINT_KEY = _RUNTIME_CHECKPOINT_KEY
    _PENDING_USER_TURN_KEY = _PENDING_USER_TURN_KEY

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        web_config: WebToolsConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_idle_timeout_minutes: int = 0,
        hooks: list[AgentHook] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        project_root: Path | None = None,
        provider_snapshot_loader: Callable[[], ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        db=None,
        pt_save_interval: int = 30,
    ):
        from nanobot.config.schema import ExecToolConfig, ToolsConfig, WebToolsConfig

        _tc = tools_config or ToolsConfig()
        defaults = AgentDefaults()
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self._provider_snapshot_loader = provider_snapshot_loader
        self._provider_signature = provider_signature
        self._db = db
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        self.context_window_tokens = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.web_config = web_config or WebToolsConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self.project_root = project_root
        self._extra_hooks: list[AgentHook] = hooks or []
        self._extra_hooks.extend(self._discover_hooks())

        self._init_framework_dir(workspace)
        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills, db=db,
                                       project_root=project_root)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider, db=db)
        self._recovery = RecoveryManager(self)
        self._dispatch_manager = DispatchManager(self)
        self._system_handler = SystemMessageHandler(self)
        self._user_handler = UserMessageHandler(self)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_config=self.web_config,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
            db=db,
            timezone=self.context.timezone,
            project_root=self.project_root,
            memory_store=self.context.memory,
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False
        self._session_dispatch: dict[str, _SessionDispatchState] = {}
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited (default).
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "0"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        from nanobot.utils.helpers import ensure_dir
        self.prompts_dir = ensure_dir(workspace / "prompts")
        self._pt_counters: dict[str, int] = {}
        self._pt_save_interval = pt_save_interval
        self.extractor = MemoryExtractor(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            timezone=self.context.timezone,
        )
        self._register_default_tools()
        if _tc.my.enable:
            self.tools.register(MyTool(loop=self, modify_allowed=_tc.my.allow_set))
        self._runtime_vars: dict[str, Any] = {}
        self._current_iteration: int = 0
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)
        self.context.warmup()
        # Per-session observe toggles — keyed by session_key
        # Format: {_observe_think: {session_key: bool}, _observe_tool: {session_key: bool}, _observe_debug: {session_key: bool}}
        self._session_observe: dict[str, dict[str, bool]] = {
            "_observe_think": {},
            "_observe_tool": {},
            "_observe_debug": {},
        }

    # ------------------------------------------------------------------
    # Backward-compat properties for _session_dispatch
    # ------------------------------------------------------------------

    @property
    def _pending_queues(self) -> dict[str, asyncio.Queue]:
        return {k: v.pending for k, v in self._session_dispatch.items()}

    @property
    def _active_tasks(self) -> dict[str, list[asyncio.Task]]:
        return {k: v.tasks for k, v in self._session_dispatch.items()}

    # ------------------------------------------------------------------
    # Observe events — /think and /tool
    # ------------------------------------------------------------------

    def _emit_observe_event(
        self,
        event_type: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        """Emit a /think or /tool progress event to the proxy channel.

        Called from runner/hook callbacks to push real-time events to the user.
        """
        from nanobot.agent.context_vars import _current_inbound

        inbound = _current_inbound.get()
        if inbound is None:
            return

        # Skip if observe toggle is off for this session
        session_key = self._dispatch_manager._effective_session_key(inbound)
        if event_type == "thinking":
            if not self._session_observe["_observe_think"].get(session_key, False):
                return
        elif event_type.startswith("tool_"):
            if not self._session_observe["_observe_tool"].get(session_key, False):
                return
        else:
            return

        msg = OutboundMessage(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            content=content,
            metadata=metadata,
        )
        self.bus.publish_outbound(msg)

    def _apply_provider_snapshot(self, snapshot: ProviderSnapshot) -> None:
        """Swap model/provider for future turns without disturbing an active one."""
        provider = snapshot.provider
        model = snapshot.model
        context_window_tokens = snapshot.context_window_tokens
        if self.provider is provider and self.model == model:
            return
        old_model = self.model
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self.runner.provider = provider
        self.subagents.set_provider(provider, model)
        self.extractor.set_provider(provider, model)
        self._provider_signature = snapshot.signature
        logger.info("Runtime model switched for next turn: {} -> {}", old_model, model)

    def _refresh_provider_snapshot(self) -> None:
        if self._provider_snapshot_loader is None:
            return
        try:
            snapshot = self._provider_snapshot_loader()
        except Exception:
            logger.exception("Failed to refresh provider config")
            return
        if snapshot.signature == self._provider_signature:
            return
        self._apply_provider_snapshot(snapshot)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = (
            self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        )
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(AskUserTool())
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool, DeleteFileTool, MoveFileTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy, user_agent=self.web_config.user_agent)
            )
            self.tools.register(WebFetchTool(config=self.web_config.fetch, proxy=self.web_config.proxy, user_agent=self.web_config.user_agent))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound, workspace=self.workspace))
        self.tools.register(MemorySearchTool(store=self.context.memory))
        self.tools.register(FrameworkSearchTool(store=self.context.memory))
        self.tools.register(ConversationSearchTool(store=self.context.memory))
        self.tools.register(SearchTextTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ReadFilesTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExploreModuleTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(GitInspectTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(AnalyzeTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(DiagnoseTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ScanProjectTool(loop=self))
        if self._db:
            from nanobot.agent.tools.tool_call_log import ToolCallLogTool
            self.tools.register(ToolCallLogTool(db=self._db))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(CheckSubagentTool(manager=self.subagents))
        self.tools.register(ListSubagentsTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )
        # Exec is registered LAST so workspace interaction tools (read_file,
        # grep, glob, etc.) appear first in the LLM's tool list. When the LLM
        # reaches for a task, it sees workspace tools before exec — nudging it
        # toward the right tool for interaction tasks without blocking
        # computational exec use (data processing, scripts, builds).
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        await _connect_mcp(self)

    def _set_tool_context(
        self, channel: str, chat_id: str,
        message_id: str | None = None, metadata: dict | None = None,
        session_key: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info."""
        # When the caller threads a thread-scoped session_key (e.g. slack with
        # reply_in_thread: true), honor it so spawn announces route back to
        # the originating thread session. Falls back to unified mode or
        # channel:chat_id for callers that don't have a thread-scoped key.
        if session_key is not None:
            effective_key = session_key
        elif self._unified_session:
            effective_key = UNIFIED_SESSION_KEY
        else:
            effective_key = f"{channel}:{chat_id}"
        for name in ("message", "spawn", "cron", "my"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "spawn":
                        tool.set_context(channel, chat_id, effective_key=effective_key)
                    elif name == "cron":
                        tool.set_context(channel, chat_id, metadata=metadata, session_key=session_key)
                    elif name == "message":
                        tool.set_context(channel, chat_id, message_id, metadata=metadata)
                    else:
                        tool.set_context(channel, chat_id)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from nanobot.agent.loop_utils import strip_think

        return strip_think(text) or None

    @staticmethod
    def _runtime_chat_id(msg: InboundMessage) -> str:
        """Return the chat id shown in runtime metadata for the model."""
        return str(msg.metadata.get("context_chat_id") or msg.chat_id)

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from nanobot.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    async def _dispatch_command_inline(
        self,
        msg: InboundMessage,
        key: str,
        raw: str,
        dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """Dispatch a command directly from the run() loop and publish the result."""
        ctx = CommandContext(msg=msg, session=None, key=key, raw=raw, loop=self)
        result = await dispatch_fn(ctx)
        if result:
            await self.bus.publish_outbound(result)
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        state = self._session_dispatch.pop(key, None)
        tasks = state.tasks if state else []
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("Error during task cancellation")
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _get_session_key_for_chat(self, chat_id: str, channel: str) -> str:
        """Derive session_key from chat_id and channel for observe toggle commands.

        Uses the same convention as proxy messages: channel:chat_id.
        """
        return f"{channel}:{chat_id}"

    def _compute_history_budget(self) -> int:
        """Budget for history replay — leave room for output, no artificial caps."""
        if self.context_window_tokens <= 0:
            return 0
        max_output = getattr(getattr(self.provider, "generation", None), "max_tokens", 4096)
        try:
            reserved_output = int(max_output)
        except (TypeError, ValueError):
            reserved_output = 4096
        # Let the model use its actual configured output space.
        # The provider API enforces the real context limit — we just need
        # a rough cap so history doesn't crowd out the entire window.
        budget = self.context_window_tokens - max(1, reserved_output) - 4096
        return budget if budget > 0 else max(4096, self.context_window_tokens // 4)

    # ------------------------------------------------------------------
    # Investigate / Verify dispatcher
    # ------------------------------------------------------------------

    _INVESTIGATE_RE = re.compile(
        r'✅\s*investigate:\s*(\w+)\(([^)]+)\)\s*'
    )
    _VERIFY_RE = re.compile(
        r'✅\s*verify:\s*(\w+)\(([^)]+)\)\s*'
    )

    _COMPLETION_PATTERNS = re.compile(
        r'(?i)(?<![a-z])('
        r'(?<!未)完成|做完了|全部完成|任务完成|'
        r'done|finished|completed|all done|task complete'
        r')(?![a-z])'
    )

    @staticmethod
    def _has_completion_marker(text: str) -> bool:
        """Check if the tail of *text* signals task completion."""
        if not text:
            return False
        return bool(AgentLoop._COMPLETION_PATTERNS.search(text[-400:]))

    def _scan_iv_markers(self, text: str) -> list[tuple[str, str, list[str]]]:
        """Scan text for investigate/verify markers.

        Returns list of (kind, action_type, args) tuples.
        """
        markers: list[tuple[str, str, list[str]]] = []
        for match in self._INVESTIGATE_RE.finditer(text):
            action_type = match.group(1)
            args = self._parse_iv_args(match.group(2))
            markers.append(("investigate", action_type, args))
        for match in self._VERIFY_RE.finditer(text):
            action_type = match.group(1)
            args = self._parse_iv_args(match.group(2))
            markers.append(("verify", action_type, args))
        return markers

    @staticmethod
    def _strip_iv_markers(text: str) -> str:
        """Remove investigate/verify markers from text, keeping surrounding content."""
        result = re.sub(
            r'✅\s*(investigate|verify):\s*\w+\([^)]+\)\s*',
            '',
            text,
        )
        return result.strip()

    @staticmethod
    def _parse_iv_args(args_str: str) -> list[str]:
        """Parse quoted arguments from an investigate/verify marker.

        Handles single and double quoted args, e.g.:
            'pattern', 'file'  →  ['pattern', 'file']
            "some path"        →  ['some path']
        """
        parts: list[str] = []
        current: list[str] = []
        in_quote: str | None = None
        for ch in args_str.strip():
            if in_quote:
                if ch == in_quote:
                    in_quote = None
                else:
                    current.append(ch)
            elif ch in ("'", '"'):
                in_quote = ch
            elif ch == ",":
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current).strip())
        return parts

    async def _execute_investigate_verify(
        self, markers: list[tuple[str, str, list[str]]]
    ) -> list[str]:
        """Execute investigate/verify markers and return result lines.

        Each marker is (kind, action_type, args) where kind='investigate'|'verify'.
        """
        results: list[str] = []
        for kind, action_type, args in markers:
            if action_type == "file_exists":
                path = args[0] if args else ""
                exists = os.path.exists(path)
                results.append(f"✅ {kind} file_exists('{path}'): {'exists' if exists else 'not found'}")
            elif action_type == "grep":
                pattern = args[0] if len(args) > 0 else ""
                filepath = args[1] if len(args) > 1 else ""
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        matches = [line.rstrip() for line in f if re.search(pattern, line)]
                    if matches:
                        lines = "\n".join(matches[:20])
                        results.append(f"✅ {kind} grep('{pattern}', '{filepath}'):\n{lines}")
                    else:
                        results.append(f"✅ {kind} grep('{pattern}', '{filepath}'): no matches")
                except Exception as e:
                    results.append(f"✅ {kind} grep('{pattern}', '{filepath}'): error — {e}")
            elif action_type == "exit_zero":
                cmd = args[0] if args else ""
                try:
                    r = await asyncio.to_thread(
                        subprocess.run, cmd, shell=True, capture_output=True, text=True, timeout=30,
                    )
                    if r.returncode == 0:
                        results.append(f"✅ {kind} exit_zero('{cmd}'): passed")
                    else:
                        out = (r.stdout or "")[:200] + (r.stderr or "")[:200]
                        results.append(f"❌ {kind} exit_zero('{cmd}'): failed (code {r.returncode})\n{out}")
                except subprocess.TimeoutExpired:
                    results.append(f"❌ {kind} exit_zero('{cmd}'): timed out")
                except Exception as e:
                    results.append(f"❌ {kind} exit_zero('{cmd}'): error — {e}")
            elif action_type == "llm":
                prompt = args[0] if args else ""
                try:
                    resp = await self.provider.chat([
                        {"role": "user", "content": prompt}
                    ], model=self.model)
                    text = resp.get("content", "") or ""
                    results.append(f"✅ {kind} llm:\n{text[:500]}")
                except Exception as e:
                    results.append(f"❌ {kind} llm: error — {e}")
            elif action_type == "agent_loop":
                prompt = args[0] if args else ""
                try:
                    resp = await self.provider.chat([
                        {"role": "system", "content": "You are a research assistant. Investigate the task thoroughly and report findings."},
                        {"role": "user", "content": prompt},
                    ], model=self.model)
                    text = resp.get("content", "") or ""
                    results.append(f"✅ {kind} agent_loop:\n{text[:500]}")
                except Exception as e:
                    results.append(f"❌ {kind} agent_loop: error — {e}")
            else:
                results.append(f"❓ {kind}: unknown action type '{action_type}'")
        return results

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_reasoning: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_end: Callable[..., Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        observe_think = self._session_observe["_observe_think"].get(session_key, True)
        observe_tool = self._session_observe["_observe_tool"].get(session_key, True)
        if self._session_observe["_observe_debug"].get(session_key, False):
            _current_debug_enabled.set(True)
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_reasoning=on_reasoning,
            on_reasoning_end=on_reasoning_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            observe_think=observe_think,
            observe_tool=observe_tool,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._recovery.set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            When no messages are immediately available but sub-agents
            spawned in this dispatch are still running, blocks until at
            least one result arrives (or timeout).  This keeps the runner
            loop alive so subsequent sub-agent completions are consumed
            in-order rather than dispatched separately.
            """
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = separate_and_extract_media(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    channel=pending_msg.channel,
                    timezone=self.context.timezone,
                )
                if runtime_ctx:
                    if isinstance(user_content, str):
                        user_content = f"{runtime_ctx}\n\n{user_content}"
                    else:
                        user_content = [{"type": "text", "text": runtime_ctx}] + list(user_content)
                return {"role": "user", "content": user_content}

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            # Merge multiple drained messages into a single user message
            # to keep the conversation focused on the user's latest intent
            # rather than having the LLM reply to each injection individually.
            if len(items) > 1:
                parts: list[str] = []
                for item in items:
                    content = item["content"]
                    if isinstance(content, str):
                        parts.append(content)
                    elif isinstance(content, list):
                        texts: list[str] = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(str(block.get("text", "")))
                        if texts:
                            parts.append(" ".join(texts))
                combined = "\n\n---\n\n".join(parts)
                items = [{"role": "user", "content": combined}]

            return items

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            retry_wait_callback=on_retry_wait,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
            reasoning_effort=self.provider.generation.reasoning_effort,
        ))
        self._last_usage = result.usage

        # Post-process: scan for investigate/verify markers and re-enter if found
        if result.final_content and result.messages:
            markers = self._scan_iv_markers(result.final_content)
            if markers:
                iv_results = await self._execute_investigate_verify(markers)
                if iv_results:
                    cleaned = self._strip_iv_markers(result.final_content)
                    # Replace the last assistant message content with cleaned text
                    for i in range(len(result.messages) - 1, -1, -1):
                        if result.messages[i].get("role") == "assistant":
                            result.messages[i]["content"] = cleaned
                            break
                    result.messages.append({
                        "role": "user",
                        "content": "## Investigation/Verification Results\n\n" + "\n\n".join(iv_results),
                    })
                    result = await self.runner.run(AgentRunSpec(
                        initial_messages=result.messages,
                        tools=self.tools,
                        model=self.model,
                        max_iterations=self.max_iterations,
                        max_tool_result_chars=self.max_tool_result_chars,
                        hook=hook,
                        error_message="Sorry, I encountered an error calling the AI model.",
                        concurrent_tools=True,
                        workspace=self.workspace,
                        session_key=session.key if session else None,
                        context_window_tokens=self.context_window_tokens,
                        context_block_limit=self.context_block_limit,
                        provider_retry_mode=self.provider_retry_mode,
                        progress_callback=on_progress,
                        retry_wait_callback=on_retry_wait,
                        checkpoint_callback=_checkpoint,
                        injection_callback=_drain_pending,
                        reasoning_effort=self.provider.generation.reasoning_effort,
                    ))
                    self._last_usage = result.usage

        # Completion detection — gentle verification reminder when agent claims done
        if (result.final_content
            and result.tools_used
            and result.stop_reason not in ("max_iterations", "error", "ask_user")
        ):
            if self._has_completion_marker(result.final_content):
                result.messages.append({
                    "role": "user",
                    "content": (
                        "⚠️ Verification Reminder\n\n"
                        "You signaled completion. Before closing the turn:\n"
                        "- Have all acceptance criteria been verified?\n"
                        "- Are tests passing?\n"
                        "- Any missed requirements?\n\n"
                        "If confirmed, wrap up. If more work is needed, continue."
                    ),
                })
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=result.messages,
                    tools=self.tools,
                    model=self.model,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=hook,
                    error_message="Sorry, I encountered an error calling the AI model.",
                    concurrent_tools=True,
                    workspace=self.workspace,
                    session_key=session.key if session else None,
                    context_window_tokens=self.context_window_tokens,
                    context_block_limit=self.context_block_limit,
                    provider_retry_mode=self.provider_retry_mode,
                    progress_callback=on_progress,
                    retry_wait_callback=on_retry_wait,
                    checkpoint_callback=_checkpoint,
                    injection_callback=_drain_pending,
                    reasoning_effort=self.provider.generation.reasoning_effort,
                ))
                self._last_usage = result.usage

        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                await self._dispatch_command_inline(
                    msg, msg.session_key, raw,
                    self.commands.dispatch_priority,
                )
                continue
            effective_key = self._dispatch_manager._effective_session_key(msg)
            # If this session already has an active pending queue (i.e. a task
            # is processing this session), route the message there for mid-turn
            # injection instead of creating a competing task.
            if effective_key in self._session_dispatch:
                # Non-priority commands must not be queued for injection;
                # dispatch them directly (same pattern as priority commands).
                if self.commands.is_dispatchable_command(raw):
                    await self._dispatch_command_inline(
                        msg, effective_key, raw,
                        self.commands.dispatch,
                    )
                    continue
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self._session_dispatch[effective_key].pending.put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, creating queued task instead",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue
            # Compute the effective session key before dispatching
            # This ensures /stop command can find tasks correctly when unified session is enabled
            task = asyncio.create_task(self._dispatch(msg))
            state = self._session_dispatch.setdefault(effective_key, _SessionDispatchState(tasks=[], pending=asyncio.Queue(maxsize=20)))
            state.tasks.append(task)
            task.add_done_callback(
                lambda t, k=effective_key: (
                    self._session_dispatch[k].tasks.remove(t)
                    if k in self._session_dispatch and t in self._session_dispatch[k].tasks
                    else None
                )
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._dispatch_manager._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())

        # Register a pending queue so follow-up messages for this session are
        # routed here (mid-turn injection) instead of spawning a new task.
        pending = asyncio.Queue(maxsize=20)
        # Don't overwrite existing state (created by run()) — reuse existing queue
        if session_key not in self._session_dispatch:
            self._session_dispatch[session_key] = _SessionDispatchState(tasks=[], pending=pending)
        else:
            pending = self._session_dispatch[session_key].pending

        async with lock:
            await self._dispatch_manager.run_dispatch(msg, session_key, pending)

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        await _close_mcp(self)

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(
            lambda t: (
                self._background_tasks.remove(t)
                if t in self._background_tasks
                else None
            )
        )

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_reasoning: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        self._refresh_provider_snapshot()
        if msg.channel == "system":
            return await self._system_handler.handle(
                msg, on_stream, on_stream_end, on_reasoning, on_reasoning_end, pending_queue,
            )
        return await self._user_handler.handle(
            msg, session_key, on_progress, on_stream, on_stream_end,
            on_reasoning, on_reasoning_end, pending_queue,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _append_turn_to_session(self, session: Session, messages: list[dict], skip: int) -> None:
        """Record turn messages into session history, truncating large tool results.

        Appends ``messages[skip:]`` into ``session.messages``, stripping
        runtime-only blocks and oversized tool results. Does **not** persist
        to storage — call ``sessions.save()`` separately if needed.
        """
        from datetime import datetime, timezone

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after.startswith("--- latest user message below ---"):
                            after = after[len("--- latest user message below ---"):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now(timezone.utc)

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            timestamp=msg.timestamp.isoformat(),
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _discover_hooks(self) -> list[AgentHook]:
        """Scan framework hooks then workspace/hooks/ for custom hook classes."""
        from pathlib import Path
        discovered: list[AgentHook] = []

        # 1. Framework hooks — loaded first so workspace hooks can override
        framework_dir = Path(__file__).resolve().parent.parent / "hooks"
        if framework_dir.is_dir():
            for path in sorted(framework_dir.glob("*.py")):
                self._try_load_hook(path, discovered)

        # 2. Workspace hooks — loaded after, can override or extend
        hooks_dir = self.workspace / "hooks"
        if hooks_dir.is_dir():
            for path in sorted(hooks_dir.glob("*.py")):
                self._try_load_hook(path, discovered)

        return discovered

    @staticmethod
    def _try_load_hook(path: Path, discovered: list[AgentHook]) -> None:
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, AgentHook) and attr is not AgentHook:
                        instance = attr()
                        if hasattr(instance, "HOOK_CLASSES"):
                            for cls in instance.HOOK_CLASSES:
                                if isinstance(cls, type) and issubclass(cls, AgentHook):
                                    discovered.append(cls())
                        else:
                            discovered.append(instance)
                        logger.info("Loaded hook: {} from {}", attr_name, path.name)
        except Exception as e:
            logger.warning("Failed to load hook {}: {}", path.name, e)

    @staticmethod
    def _init_framework_dir(workspace: Path) -> None:
        """Copy bundled framework/ templates to workspace if not present."""
        target = workspace / "framework"
        if target.exists():
            return
        try:
            from importlib.resources import files as pkg_files
            src = pkg_files("nanobot.templates") / "framework"
            if not src.is_dir():
                logger.info("No bundled framework templates found")
                return
            import shutil
            shutil.copytree(str(src), str(target), dirs_exist_ok=True)
            logger.info("Initialized framework/ from bundled templates")
        except Exception:
            logger.exception("Failed to initialize framework/ directory")

    # Backward-compat wrappers delegating to module functions
    # (used by tests that patch __init__ and set attributes directly)
    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        set_runtime_checkpoint(session, payload)
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        mark_pending_user_turn(session)

    def _clear_pending_user_turn(self, session: Session) -> None:
        clear_pending_user_turn(session)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        clear_runtime_checkpoint(session)

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        return restore_and_clear_checkpoint(self, session)

    def _restore_pending_user_turn(self, session: Session) -> bool:
        return restore_pending_user_turn(session)

    def _checkpoint_message_key(self, message: dict[str, Any]) -> tuple[Any, ...]:
        return checkpoint_message_key(message)

    def process_direct_sync(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[..., None] | None = None,
        on_stream: Callable[[str], None] | None = None,
        on_stream_end: Callable[..., None] | None = None,
    ) -> OutboundMessage | None:
        """Synchronous wrapper for process_direct, for use in thread pool.

        Creates a fresh event loop in the call thread to avoid conflicts
        with the caller's event loop.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.process_direct(
                    content=content,
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                    media=media,
                    on_progress=on_progress,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                )
            )
        finally:
            loop.close()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        from nanobot.agent.context_vars import _current_inbound

        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [], metadata=metadata or {},
        )
        _current_inbound.set(msg)
        self._current_session_key = session_key
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
