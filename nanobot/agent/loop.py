"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.assess_me import is_assessment_message, is_debug_root_cause_message
from nanobot.agent.context import ContextBuilder, _sanitize_session_key
from nanobot.agent.context_vars import _current_debug_enabled
from nanobot.agent.hook import AgentHook, CompositeHook
from nanobot.agent.loop_hook import _LoopHook
from nanobot.agent.memory import MemoryExtractor
from nanobot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.analyze_tool import AnalyzeTool
from nanobot.agent.tools.assess_me_tool import AssessMeTool
from nanobot.agent.tools.cancel_subagent import CancelSubagentTool
from nanobot.agent.tools.check_subagent import CheckSubagentTool
from nanobot.agent.tools.conversation_search import ConversationSearchTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
from nanobot.agent.tools.explore_module import ExploreModuleTool
from nanobot.agent.tools.filesystem import (
    DeleteFileTool,
    EditFileTool,
    MoveFileTool,
    ReadFileTool,
    WriteFileTool,
)
from nanobot.agent.tools.list_subagents import ListSubagentsTool
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.reframe import ReframeTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.scan_project import ScanProjectTool
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.self import SelfTool
from nanobot.agent.tools.self_restart_tool import SelfRestartTool
from nanobot.agent.tools.semantic_search import SearchTextTool
from nanobot.agent.tools.send_message import SendMessageTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.stage import RestoreStageTool, SaveStageTool, ShowStagesTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command import CommandContext, CommandRouter, register_builtin_commands
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider
from nanobot.providers.factory import ProviderSnapshot
from nanobot.session.lifecycle import SessionLifecycle
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.document import separate_and_extract_media
from nanobot.utils.helpers import truncate_text as truncate_text_fn
from nanobot.utils.media_decode import image_placeholder_text

from .loop_checkpoint import (
    RecoveryManager,
    checkpoint_message_key,
    clear_pending_user_turn,
    clear_runtime_checkpoint,
    mark_pending_user_turn,
    restore_and_clear_checkpoint,
    restore_pending_user_turn,
    set_runtime_checkpoint,
)

# Import from split modules
from .loop_constants import (
    _DEFAULT_MAX_RETRIES,
    _DEFAULT_RETRY_BACKOFF_INITIAL,
    _DEFAULT_RETRY_BACKOFF_JITTER,
    _DEFAULT_RETRY_BACKOFF_MAX,
    _DEFAULT_RETRY_BACKOFF_MULTIPLIER,
    _PENDING_USER_TURN_KEY,
    _RUNTIME_CHECKPOINT_KEY,
)
from .loop_dispatch import DispatchManager
from .loop_mcp import close_mcp as _close_mcp
from .loop_mcp import connect_mcp as _connect_mcp
from .loop_message_handlers import SystemMessageHandler, UserMessageHandler

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, ToolsConfig, WebToolsConfig
    from nanobot.cron.service import CronService


@dataclasses.dataclass
class _SessionDispatchState:
    """Per-session dispatch tracking: active tasks and mid-turn injection queue."""
    tasks: list[asyncio.Task]
    pending: asyncio.Queue


@dataclasses.dataclass
class _SubagentCheckState:
    """Per-session state for subagent check throttling."""
    last_check: float
    channel: str
    chat_id: str


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
        hooks: list[AgentHook] | None = None,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        project_root: Path | None = None,
        provider_snapshot_loader: Callable[[], ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        db=None,
        pt_save_interval: int = 30,
        compress_trigger_tokens: int | None = None,
        history_token_limit: int | None = None,
        assess_interval: int | None = None,
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
        self._history_token_limit = (
            history_token_limit
            if history_token_limit is not None
            else defaults.history_token_limit
        )
        self._compress_trigger_tokens = (
            compress_trigger_tokens
            if compress_trigger_tokens is not None
            else int(self._history_token_limit * 1.5)
        )
        self.assess_interval = assess_interval if assess_interval is not None else defaults.assess_interval
        self.project_root = project_root
        self._skill_creation_inflight: set[str] = set()
        self._extra_hooks: list[AgentHook] = hooks or []
        self._extra_hooks.extend(self._discover_hooks())

        self._init_framework_dir(workspace)
        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills, db=db,
                                       project_root=project_root, framework_config={
                                           "max_iterations": self.max_iterations,
                                           "context_window_tokens": self.context_window_tokens,
                                           "max_tool_result_chars": self.max_tool_result_chars,
                                           "exec_timeout": self.exec_config.timeout,
                                           "subagent_max_iterations": 100,
                                           "heartbeat_interval_minutes": 30,
                                           "model": self.model,
                                           "provider": provider.__class__.__name__,
                                           "reasoning_effort": provider.generation.reasoning_effort,
                                       })
        self.sessions = session_manager or SessionManager()
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider, db=db)
        self._recovery = RecoveryManager(self)
        self.lifecycle = SessionLifecycle(self.sessions, self._recovery)
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
            context_builder=self.context,
        )
        self._running = False
        self._last_subagent_check: dict[str, _SubagentCheckState] = {}
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False
        self._session_dispatch: dict[str, _SessionDispatchState] = {}
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_lock_last_used: dict[str, float] = {}  # for LRU pruning
        self._session_lock_prune_counter = 0
        self._MAX_SESSION_LOCKS = 10000
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited (default).
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "0"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        from nanobot.utils.helpers import ensure_dir
        self.prompts_dir = ensure_dir(workspace / "prompts")
        self._pt_save_interval = pt_save_interval
        self.extractor = MemoryExtractor(
            store=self.context.memory,
            timezone=self.context.timezone,
        )
        self._register_default_tools()
        if _tc.my.enable:
            self.tools.register(SelfTool(loop=self, modify_allowed=_tc.my.allow_set))
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
        # Retry / backoff / checkpoint state
        # ------------------------------------------------------------------
        self.retry_count: int = 0          # total retries performed across this turn
        self.max_retries: int = _DEFAULT_MAX_RETRIES
        self.backoff_initial: float = _DEFAULT_RETRY_BACKOFF_INITIAL
        self.backoff_max: float = _DEFAULT_RETRY_BACKOFF_MAX
        self.backoff_multiplier: float = _DEFAULT_RETRY_BACKOFF_MULTIPLIER
        self.backoff_jitter: float = _DEFAULT_RETRY_BACKOFF_JITTER


    # ------------------------------------------------------------------
    # Backward-compat properties for _session_dispatch
    # ------------------------------------------------------------------

    @property
    def _pending_queues(self) -> dict[str, asyncio.Queue]:
        return {k: v.pending for k, v in self._session_dispatch.items()}

    @property
    def _active_tasks(self) -> dict[str, list[asyncio.Task]]:
        return {k: v.tasks for k, v in self._session_dispatch.items()}

    # -- Public API for command handlers -----------------------------------

    @property
    def last_usage(self) -> dict[str, int]:
        """Token usage from the most recent provider call."""
        return self._last_usage

    @property
    def start_time(self) -> float:
        """Monotonic timestamp of when this loop was started."""
        return self._start_time

    @property
    def active_tasks(self) -> dict[str, list[asyncio.Task]]:
        """Active asyncio tasks grouped by session key."""
        return self._active_tasks

    async def cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*."""
        return await self._cancel_active_tasks(key)

    # ------------------------------------------------------------------
    # Retry / backoff helpers
    # ------------------------------------------------------------------

    def reset_retry_state(self) -> None:
        """Reset retry counters at the start of a new dispatch or turn."""
        self.retry_count = 0

    # ------------------------------------------------------------------
    # Provider snapshot
    # ------------------------------------------------------------------

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
        from nanobot.agent.llm_context import set_llm
        set_llm(provider, model)
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
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, DeleteFileTool, MoveFileTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy, user_agent=self.web_config.user_agent)
            )
            self.tools.register(WebFetchTool(config=self.web_config.fetch, proxy=self.web_config.proxy, user_agent=self.web_config.user_agent))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound, workspace=self.workspace))
        self.tools.register(MemorySearchTool(store=self.context.memory))
        self.tools.register(ConversationSearchTool(store=self.context.memory))
        self.tools.register(SearchTextTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExploreModuleTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(SaveStageTool())
        self.tools.register(ShowStagesTool())
        self.tools.register(RestoreStageTool())
        self.tools.register(AnalyzeTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ScanProjectTool(loop=self))
        self.tools.register(ReframeTool(workspace=self.workspace))
        self.tools.register(DebugRootCauseTool())
        self.tools.register(AssessMeTool())
        if self._db:
            from nanobot.agent.tools.tool_call_log import ToolCallLogTool
            self.tools.register(ToolCallLogTool(db=self._db))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(CheckSubagentTool(manager=self.subagents))
        self.tools.register(CancelSubagentTool(manager=self.subagents))
        self.tools.register(ListSubagentsTool(manager=self.subagents))
        self.tools.register(SendMessageTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )
        self.tools.register(SelfRestartTool())
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
        else:
            effective_key = f"{channel}:{chat_id}"

        # Format session history for tools that need conversation context
        session = self.sessions.get_or_create(effective_key)
        history = session.format_history(
            include_timestamps=True, timezone=self.context.timezone
        )

        for name in ("message_tool", "spawn_tool", "cron_tool", "self_tool", "assess_me_tool", "debug_root_cause_tool"):
            tool = self.tools.get(name)
            if tool is None:
                continue
            sc = getattr(tool, "set_context", None)
            if not sc:
                continue
            if name == "spawn_tool":
                sc(channel, chat_id, effective_key=effective_key)
            elif name == "cron_tool":
                sc(channel, chat_id, metadata=metadata, session_key=session_key)
            elif name in ("assess_me_tool", "debug_root_cause_tool"):
                sc(messages=history)
            elif name == "message_tool":
                sc(channel, chat_id, message_id, metadata=metadata)
            else:
                sc(channel, chat_id)
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
                logger.warning("Error during task cancellation", exc_info=True)
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def get_session_lock(self, session_key: str) -> asyncio.Lock:
        """Return (or create) the per-session ``asyncio.Lock`` for *session_key*.

        Callers outside ``_dispatch`` (e.g. slash command handlers) must
        acquire this lock before mutating session state to prevent races
        with the active dispatch task.
        """
        self._session_lock_last_used[session_key] = time.time()
        return self._session_locks.setdefault(session_key, asyncio.Lock())

    def _prune_session_locks(self) -> None:
        """Remove session locks not in active dispatch when over capacity.

        Prevents unbounded growth of _session_locks when many unique
        session_keys pass through over the lifetime of the process.
        Active dispatch sessions (tracked in _session_dispatch) are
        always preserved.
        """
        if len(self._session_locks) <= self._MAX_SESSION_LOCKS:
            return
        now = time.time()
        stale_threshold = now - 3600  # 1 hour idle
        for key in list(self._session_locks.keys()):
            if key in self._session_dispatch:
                continue
            last_used = self._session_lock_last_used.get(key, 0)
            if last_used < stale_threshold:
                del self._session_locks[key]
                self._session_lock_last_used.pop(key, None)

    def _get_session_key_for_chat(self, chat_id: str, channel: str) -> str:
        """Derive session_key from chat_id and channel for observe toggle commands.

        Uses the same convention as proxy messages: channel:chat_id.
        """
        return f"{channel}:{chat_id}"

    # (IV markers and completion detection removed)

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
    ) -> tuple[str | None, list[str], list[dict], str, bool, int, int]:
        logger.info("RUN_DBG: _run_agent_loop start ({} messages)", len(initial_messages))
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        observe_think = self._session_observe["_observe_think"].get(session_key or "", True)
        observe_tool = self._session_observe["_observe_tool"].get(session_key or "", True)
        if self._session_observe["_observe_debug"].get(session_key or "", False):
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

        # Reset retry counters at the start of each turn
        self.reset_retry_state()

        # Build backoff config and retry context from loop attributes
        from nanobot.agent.runner_retry import BackoffConfig, RetryContext
        backoff_cfg = BackoffConfig(
            initial_delay=self.backoff_initial,
            max_delay=self.backoff_max,
            multiplier=self.backoff_multiplier,
            jitter=self.backoff_jitter,
        )
        retry_ctx = RetryContext()

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
                from nanobot.utils.helpers import current_time_str
                runtime_lines = [f"Current Time: {current_time_str(self.context.timezone)}"]
                if pending_msg.channel:
                    runtime_lines.append(f"Channel: {pending_msg.channel}")
                runtime_ctx = "\n".join(runtime_lines)
                user_content = self.context._build_user_content(content, media)
                if runtime_ctx:
                    if isinstance(user_content, str):
                        user_content = f"{runtime_ctx}\n\n{user_content}"
                    else:
                        user_content = [{"type": "text", "text": runtime_ctx}] + list(user_content)
                return {"role": "user", "content": user_content}

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    msg = pending_queue.get_nowait()
                    items.append(_to_user_message(msg))
                    logger.debug("INJECT_DBG: drained {} item(s) from pending_queue, content='{}'", len(items), (msg.content or "")[:60])
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

        # Build instructions for runner-level injection (fresh before every LLM call)
        instructions = self.context.build_instructions_section(session_key=session.key if session else None)

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=False,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            history_token_limit=self._history_token_limit,
            compress_trigger_tokens=self._compress_trigger_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            retry_wait_callback=on_retry_wait,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
            reasoning_effort=self.provider.generation.reasoning_effort,
            retry_context=retry_ctx,
            backoff_config=backoff_cfg,
            max_llm_retries=self.max_retries,
            max_overflow_retries=self.max_retries,
            assess_interval=self.assess_interval,
            assess_me_callback=self._make_retry_assess_callback(session),
            previous_summary=getattr(session, "_last_summary", None),
            instructions=instructions,
            prompts_dir=self.prompts_dir,
            pt_save_interval=self._pt_save_interval,
        ))
        if result.overflow_summary:
            session._last_summary = result.overflow_summary
            session.metadata.pop("_summary_injected_key", None)
        # Track total retries and token usage across this turn
        self.retry_count += result.retry_count
        self._last_usage = result.usage

        # (IV markers re-entry and completion detection removed)

        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])

        await hook.after_turn()
        return (result.final_content, result.tools_used, result.messages,
                result.stop_reason, result.had_injections, result.initial_message_count,
                result.total_llm_requests)

    def _make_retry_assess_callback(self, session: Session | None):
        """Build assess_me callback for runner retry paths.

        Handles: assess_me → inject → debug_root_cause chain → skill creation.
        """
        if session is None:
            return None
        loop = self  # capture loop ref for inner use

        async def _cb(messages: list[dict]) -> bool:
            from nanobot.agent.assess_me import (
                assess_me,
                build_assessment_message,
                build_debug_root_cause_message,
            )
            try:
                logger.info("assess_me call start")
                result = await assess_me(messages)
                logger.info("assess_me call done (result_len={})", len(result) if result else 0)
            except Exception:
                logger.exception("assess_me failed")
                return False
            if not result:
                logger.info("assess_me call returned empty")
                return False
            if result.strip().startswith("[status:ok]"):
                logger.info("assess_me: all clear, no action needed")
                return False
            # Keep at most one assess_me message — remove all stale ones before injecting new
            for i in range(len(messages) - 1, -1, -1):
                if is_assessment_message(messages[i]):
                    messages.pop(i)
            messages.append(build_assessment_message(result))

            # Chain: assess_me → debug_root_cause (only when assess_me signals need)
            _needs_drc = result.strip().endswith("[need_drc]")
            if _needs_drc:
                clean_result = result.strip()[:-len("[need_drc]")].strip()
                try:
                    from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
                    logger.info("debug_root_cause call start")
                    dcr = DebugRootCauseTool()
                    dcr.set_context(messages)
                    dcr_result = await dcr.execute(problem=clean_result)
                    logger.info("debug_root_cause call done (result_len={})", len(dcr_result) if dcr_result else 0)
                    # Don't inject error messages as analysis
                    if dcr_result and not dcr_result.startswith("Error:"):
                        # Keep at most one DRC message — remove all stale ones before injecting new
                        for i in range(len(messages) - 1, -1, -1):
                            if is_debug_root_cause_message(messages[i]):
                                messages.pop(i)
                        messages.append(build_debug_root_cause_message(dcr_result))
                        logger.info("debug_root_cause injected")
                except Exception:
                    logger.exception("debug_root_cause failed")
            else:
                logger.info("debug_root_cause skipped — no [need_drc] signal from assess_me")

            # Detect skill creation opportunity
            if "值得创建 skill" in result:
                import hashlib
                dedup_key = hashlib.md5(result.encode()).hexdigest()
                if dedup_key not in loop._skill_creation_inflight:
                    loop._skill_creation_inflight.add(dedup_key)
                    task = asyncio.create_task(
                        loop._spawn_skill_creator(result, session_key=session.key if session else None),
                    )
                    task.add_done_callback(
                        lambda t: (
                            loop._skill_creation_inflight.discard(dedup_key),
                            logger.error("Skill creation failed: {}", t.exception())
                            if t.exception() else None,
                        )
                    )
                    logger.info("assess_me detected reusable pattern — spawning skill creation")
                else:
                    logger.info("Skill creation already in-flight for this pattern — skipping")

            return True
        return _cb

    async def _spawn_skill_creator(self, assess_result: str, session_key: str | None = None) -> None:
        """Spawn a background agent to create/update skill from assess_me observation."""
        from nanobot.agent.runner import AgentRunner, AgentRunSpec
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.search import GlobTool, GrepTool
        from nanobot.agent.tools.shell import ExecTool
        from nanobot.utils.prompt_templates import render_template

        logger.info("Skill creation: building agent for assess_me observation")

        tools = ToolRegistry()
        tools.register(ReadFileTool(workspace=self.workspace))
        tools.register(WriteFileTool(workspace=self.workspace))
        tools.register(EditFileTool(workspace=self.workspace))
        tools.register(GlobTool(workspace=self.workspace))
        tools.register(GrepTool(workspace=self.workspace))
        if self.exec_config and self.exec_config.enable:
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
            ))

        system_prompt = render_template(
            "agent/_instructions/skill_creation.md",
            assess_result=assess_result,
            workspace_path=self.workspace.as_posix(),
        )

        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": assess_result},
            ],
            tools=tools,
            model=self.model,
            max_iterations=10,
            max_tool_result_chars=self.max_tool_result_chars,
            session_key=session_key,
        )

        runner = AgentRunner(self.provider, db=self._db)
        result = await runner.run(spec)

        if result.final_content:
            logger.info("Skill creation agent completed: {}", result.final_content[:200])
        else:
            logger.info("Skill creation agent completed with no output")

        # Git commit any skills the sub-agent created or modified
        from nanobot.utils.gitstore import commit_workspace_changes
        commit_workspace_changes(
            self.workspace,
            rel_dirs=["skills"],
            message="skill: create/update from assess_me",
        )

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        try:
            while self._running:
                # Check for restart flag at safe point (start of each iteration)
                restart_flag = Path.home() / ".nanobot" / "workspace" / "_restart_flag.json"
                if restart_flag.exists():
                    logger.info("Restart flag detected — stopping agent loop for graceful restart")
                    self._running = False
                    break

                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    await self._check_subagents()
                    continue
                except asyncio.CancelledError:
                    if not self._running:
                        raise
                    continue
                except Exception as e:
                    logger.warning("Error consuming inbound message: {}, continuing...", e, exc_info=True)
                    continue

                raw = msg.content.strip()
                if self.commands.is_priority(raw):
                    # Check if session was busy before /stop clears dispatch state
                    _pre_stop_busy = (
                        raw.lower() == "/stop"
                        and self._dispatch_manager._effective_session_key(msg) in self._session_dispatch
                    )
                    _pre_new_busy = (
                        raw.lower() in ("/new", "/clear", "/reset")
                        and self._dispatch_manager._effective_session_key(msg) in self._session_dispatch
                    )
                    await self._dispatch_command_inline(
                        msg, msg.session_key, raw,
                        self.commands.dispatch_priority,
                    )
                    # After /stop cancels tasks, feed it to LLM so it can
                    # update tree.json (active → paused) and confirm with user.
                    if _pre_stop_busy:
                        asyncio.create_task(self._dispatch(InboundMessage(
                            channel=msg.channel, sender_id=msg.sender_id,
                            chat_id=msg.chat_id, content="/stop",
                            media=[], metadata={"_stop_redispatch": True},
                        )))
                    # Same for /new/clear/reset — feed to LLM after cancellation
                    # so it can update tree.json and confirm with user.
                    if _pre_new_busy:
                        asyncio.create_task(self._dispatch(InboundMessage(
                            channel=msg.channel, sender_id=msg.sender_id,
                            chat_id=msg.chat_id, content=raw,
                            media=[], metadata={"_new_redispatch": True},
                        )))
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
                task = asyncio.create_task(self._dispatch(msg))
                state = self._session_dispatch.setdefault(effective_key, _SessionDispatchState(tasks=[], pending=asyncio.Queue(maxsize=200)))
                state.tasks.append(task)
                task.add_done_callback(
                    lambda t, k=effective_key: (
                        self._session_dispatch[k].tasks.remove(t)
                        if k in self._session_dispatch and t in self._session_dispatch[k].tasks
                        else None
                    )
                )
        except BaseException:
            logger.exception("run() exiting due to unhandled exception")
            raise

    _PROACTIVE_CHECK_INTERVAL: float = 180.0     # LLM-triggering proactive check interval (~3 min)

    async def _publish_subagent_check(self, session_key: str, channel: str, chat_id: str) -> None:
        """Publish a proactive subagent check message to the bus.

        Routes through UserMessageHandler which detects the proactive_check
        metadata flag and injects the two-message pattern (assistant self-reminder
        + user directive) for API-compliant delivery.
        """
        count = self.subagents.get_running_count_by_session(session_key)
        suffix = f"_{_sanitize_session_key(session_key)}" if session_key else ""
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=(
                f"⏰ 主动调度检查（{count} 个 Subagent 运行中）：\n"
                "记住原始任务目标，所有决策围绕最终交付。\n"
                "\n"
                "这是主动性机会，你是项目经理，请自主判断下一步行动：\n"
                "\n"
                "**进度跟踪**\n"
                "• Subagent 进展如何？用 list_subagents_tool / check_subagent_tool 检查状态，有没卡住或完成的\n"
                f"• 读 team_board{suffix}.md — subagent 可能写了事实发现、踩坑、洞察，需要你关注或同步\n"
                f"• 读 tree{suffix}.json — 检查 task backlog，决定下一步调度\n"
                "• 需要你回复或指导某个 subagent 吗？→ send_message_tool 发送指令\n"
                "• 发现信息不对称？→ send_message_tool 主动告知，不要等 subagent 来问\n"
                "• 某个 subagent 的结果影响其他 subagent？→ 协调同步\n"
                "\n"
                "**调度决策**\n"
                "• 跑偏/无进展的 subagent → cancel\n"
                "• 还有可并行的任务 → spawn 更多\n"
                "• 当前方向还对吗？用户需求变了？→ 评估影响，cancel+重 spawn\n"
                "• 拆解有问题？→ 收半成品，重新分解再 spawn\n"
                "\n"
                "**质量与迭代**\n"
                "• Subagent 频繁卡住或产出不达标？→ 不是硬扛，是调 prompt 重 spawn\n"
                "• 某类 task 多次做不好？→ 是拆法问题，不是 subagent 的问题，迭代拆解方式\n"
                "• Subagent 完事了但质量不满意？→ 接受/重做/部分重做/重新拆解\n"
                "\n"
                "**收尾与输出**\n"
                f"• 全部结束了？→ 分析结果、更新 tree{suffix}.json、综合汇报\n"
                "• 中间结果需要记录到 memory/文档吗？\n"
                "• 需要向用户汇报进度或请示决策吗？\n"
                "\n"
                "你决定做什么。一切正常无需行动就简短确认，不用长篇大论。\n\n"
                "请继续按计划推进。"
            ),
            ephemeral=True,
            metadata={"proactive_check": True},
            session_key_override=session_key,
        )
        await self.bus.publish_inbound(msg)

    async def _check_subagents(self) -> None:
        """Proactive subagent check when the loop would otherwise sit idle.

        Only processes sessions already tracked by _dispatch (post-dispatch
        check), which stores the correct channel/chat_id from the InboundMessage.
        Never infers chat_id from session_key since it may contain sender_id
        (e.g. ou_xxx) rather than the actual chat_id (e.g. oc_xxx).
        """
        now = time.time()

        for session_key in list(self._last_subagent_check.keys()):
            state = self._last_subagent_check[session_key]
            if self.subagents.get_running_count_by_session(session_key) == 0:
                del self._last_subagent_check[session_key]
                continue
            if now - state.last_check < self._PROACTIVE_CHECK_INTERVAL:
                continue

            state.last_check = now
            await self._publish_subagent_check(session_key, state.channel, state.chat_id)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent.

        Cleans up the dispatch state on exit so the next message for this
        session creates a fresh dispatch instead of rotting in the stale
        pending queue.
        """
        session_key = self._dispatch_manager._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        self._session_lock_last_used[session_key] = time.time()

        # Register a pending queue so follow-up messages for this session are
        # routed here (mid-turn injection) instead of spawning a new task.
        pending = asyncio.Queue(maxsize=200)
        # Don't overwrite existing state (created by run()) — reuse existing queue
        if session_key not in self._session_dispatch:
            self._session_dispatch[session_key] = _SessionDispatchState(tasks=[], pending=pending)
        else:
            pending = self._session_dispatch[session_key].pending

        async with lock:
            await self._dispatch_manager.run_dispatch(msg, session_key, pending)

        # Pop ourselves so the next message for this session creates a fresh
        # dispatch.  The done_callback added by run() already handles removing
        # the task entry from state.tasks, but the stale _SessionDispatchState
        # itself must be removed here.
        self._session_dispatch.pop(session_key, None)

        # Periodic session-lock LRU pruning (every 100 dispatches)
        self._session_lock_prune_counter += 1
        if self._session_lock_prune_counter >= 100:
            self._session_lock_prune_counter = 0
            self._prune_session_locks()

        # Proactive subagent check: after dispatch, if subagents still
        # running for this session, inject a check so the agent actively
        # pulls their status instead of passively waiting for push results.
        # This is the primary active mechanism — _check_subagents is safety net.
        if self.subagents.get_running_count_by_session(session_key) > 0:
            now = time.time()
            state = self._last_subagent_check.get(session_key)
            last = state.last_check if state else 0
            if now - last >= self._PROACTIVE_CHECK_INTERVAL:
                self._last_subagent_check[session_key] = _SubagentCheckState(now, msg.channel, msg.chat_id)
                await self._publish_subagent_check(session_key, msg.channel, msg.chat_id)

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
            if role == "assistant" and not entry.get("tool_calls") and not (content and content.strip() if isinstance(content, str) else content):
                continue  # skip empty assistant messages — they poison session context
            if is_assessment_message(entry) or is_debug_root_cause_message(entry):
                continue  # skip ephemeral framework-injected messages — not part of real conversation
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
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        task_id = meta.get("subagent_task_id")
        injected_event = meta.get("injected_event", "subagent_result")

        # Only dedup actual subagent results — subagent notifications/requests
        # don't carry a task_id and should always be appended.
        if injected_event == "subagent_result" and task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False

        session.add_message(
            "assistant",
            msg.content,
            timestamp=msg.timestamp.isoformat(),
            sender_id=msg.sender_id,
            injected_event=injected_event,
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

        # 3. Wire the active provider into hooks that opt in via set_provider().
        for hook in discovered:
            inject = getattr(hook, "set_provider", None)
            if callable(inject):
                try:
                    inject(self.provider, self.model)
                except Exception as e:
                    logger.warning(
                        "Hook {} set_provider failed: {}",
                        type(hook).__name__,
                        e,
                    )

        # 4. Wire the workspace path into hooks that opt in via set_workspace().
        for hook in discovered:
            inject = getattr(hook, "set_workspace", None)
            if callable(inject):
                try:
                    inject(self.workspace)
                except Exception as e:
                    logger.warning(
                        "Hook {} set_workspace failed: {}",
                        type(hook).__name__,
                        e,
                    )

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
                        hook_classes: tuple[type[AgentHook], ...] | None = getattr(instance, "HOOK_CLASSES", None)
                        if hook_classes:
                            for cls in hook_classes:
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
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
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
        pending_queue: asyncio.Queue | None = None,
        ephemeral: bool = False,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        from nanobot.agent.context_vars import _current_inbound

        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [], metadata=metadata or {},
            session_key_override=session_key,
            ephemeral=ephemeral,
        )
        _current_inbound.set(msg)
        self._current_session_key = session_key
        response = await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            pending_queue=pending_queue,
        )
        # Same post-dispatch monitor trigger as _dispatch() — ensures proxy
        # sessions also get proactive subagent checks.
        if self.subagents.get_running_count_by_session(session_key) > 0:
            now = time.time()
            state = self._last_subagent_check.get(session_key)
            last = state.last_check if state else 0
            if now - last >= self._PROACTIVE_CHECK_INTERVAL:
                self._last_subagent_check[session_key] = _SubagentCheckState(now, channel, chat_id)
                await self._publish_subagent_check(session_key, channel, chat_id)
        return response
