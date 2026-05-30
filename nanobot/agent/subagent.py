"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.memory_extractor import MemoryExtractor
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.context_vars import _in_subagent
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig, WebToolsConfig
from nanobot.providers.base import LLMProvider

from .subagent_status import SubagentStatus, SubagentResult, format_error_progress
from .subagent_tools import build_subagent_tools
from .subagent_prompt import build_subagent_prompt


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        db=None,
        timezone: str | None = None,
        project_root: Path | None = None,
        memory_store: Any | None = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.project_root = project_root
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.web_config = web_config or WebToolsConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.timezone = timezone
        self.db = db
        self._memory_store = memory_store
        self.runner = AgentRunner(provider, db=db)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._pending_worker_questions: dict[str, asyncio.Future] = {}
        self._worker_origin: dict[str, dict[str, str]] = {}  # task_id -> origin info
        self._worker_label_to_id: dict[str, str] = {}  # label -> task_id
        self._worker_inboxes: dict[str, "asyncio.Queue[str]"] = {}  # task_id -> inbox

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        context: str = "",
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        max_iterations: int | None = None,
        output_schema: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        # Deduplicate label if it already exists
        if display_label in self._worker_label_to_id:
            suffix = 2
            while f"{display_label}_{suffix}" in self._worker_label_to_id:
                suffix += 1
            display_label = f"{display_label}_{suffix}"

        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}
        self._worker_origin[task_id] = origin
        self._worker_label_to_id[display_label] = task_id
        self._worker_inboxes[task_id] = asyncio.Queue()

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, status, context, max_iterations, output_schema)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            self._worker_inboxes.pop(task_id, None)
            self._worker_label_to_id.pop(display_label, None)
            self._worker_origin.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
        context: str = "",
        max_iterations: int | None = None,
        output_schema: str | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            tools = build_subagent_tools(self.workspace, self.web_config, self.exec_config, self.restrict_to_workspace, self._memory_store)

            # Register team communication tools
            from nanobot.agent.tools.notify_orchestrator import NotifyOrchestratorTool
            from nanobot.agent.tools.send_message import SendMessageTool
            from nanobot.agent.tools.request_input import RequestOrchestratorInputTool
            tools.register(NotifyOrchestratorTool(
                manager=self, worker_id=task_id, worker_label=label,
            ))
            tools.register(SendMessageTool(
                manager=self, worker_id=task_id, worker_label=label,
            ))
            tools.register(RequestOrchestratorInputTool(
                manager=self, worker_id=task_id, worker_label=label,
            ))
            system_prompt = build_subagent_prompt(
                self.workspace,
                self.disabled_skills,
                timezone=getattr(self, 'timezone', None),
                db=self.db,
                tool_definitions=tools.get_definitions(),
                project_root=self.project_root,
                output_schema=output_schema,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # Mark execution as subagent context — blocks nested spawn at tool level
            token = _in_subagent.set(True)
            try:
                # Injection callback for main→subagent messages
                inbox = self._worker_inboxes.get(task_id)

                async def _drain_inbox(*, limit: int = 10) -> list[dict[str, Any]]:
                    if inbox is None:
                        return []
                    items: list[dict[str, Any]] = []
                    while len(items) < limit:
                        try:
                            text = inbox.get_nowait()
                            items.append({"role": "user", "content": text})
                        except asyncio.QueueEmpty:
                            break
                    return items

                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    model=self.model,
                    max_iterations=max_iterations or 100,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status),
                    max_iterations_message="Task completed but no final response was generated.",
                    error_message=None,
                    fail_on_tool_error=True,
                    checkpoint_callback=_on_checkpoint,
                    injection_callback=_drain_inbox,
                    reasoning_effort=self.runner.provider.generation.reasoning_effort,
                    session_key=origin["session_key"],
                ))

                # Save conversation snapshot for MemoryExtractor
                pt_path = MemoryExtractor.save_prompt_snapshot(
                    result.messages,
                    self.workspace / "prompts",
                    f"subagent:{task_id}",
                )

                status.phase = "done"
                status.stop_reason = result.stop_reason
                status.completed_at = time.monotonic()
                status.tools_ran = list(result.tools_used)

                duration_s = status.completed_at - status.started_at
                token_usage = dict(result.usage or {})

                sub_result = SubagentResult(
                    task_id=task_id,
                    label=label,
                    status="ok" if result.stop_reason in ("completed", "stop", "empty_final_response") else "error",
                    final_content=result.final_content,
                    tools_used=list(result.tools_used),
                    duration_s=duration_s,
                    iteration_count=status.iteration,
                    token_usage=token_usage,
                    errors=[result.error] if result.error else [],
                    output_schema=output_schema,
                )

                if result.stop_reason == "tool_error":
                    status.tool_events = list(result.tool_events)
                    await self._announce_result(
                        task_id, label, task,
                        format_error_progress(result),
                        origin, "error", sub_result=sub_result, pt_path=pt_path,
                    )
                elif result.stop_reason == "error":
                    await self._announce_result(
                        task_id, label, task,
                        result.error or "Error: subagent execution failed.",
                        origin, "error", sub_result=sub_result, pt_path=pt_path,
                    )
                else:
                    final_result = result.final_content or "Task completed but no final response was generated."
                    logger.info("Subagent [{}] completed successfully", task_id)
                    await self._announce_result(task_id, label, task, final_result, origin, "ok", sub_result=sub_result, pt_path=pt_path)
            finally:
                _in_subagent.reset(token)

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        sub_result: SubagentResult | None = None,
        pt_path: Path | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        from nanobot.utils.prompt_templates import render_template

        status_text = "completed successfully" if status == "ok" else "failed"
        _schema = sub_result.output_schema if sub_result else None
        _pt_path = str(pt_path) if pt_path else ""

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
            duration_s=sub_result.duration_s if sub_result else 0,
            tools_used=", ".join(sub_result.tools_used) if sub_result and sub_result.tools_used else "",
            iteration_count=sub_result.iteration_count if sub_result else 0,
            status=status,
            output_schema=_schema,
            pt_path=_pt_path,
        )

        # Wrap in <system-reminder> so the main agent's LLM clearly
        # distinguishes this as system-injected context, not user input.
        wrapped = f"<system-reminder>\n{announce_content}\n</system-reminder>"

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=wrapped,
            session_key_override=override,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_status(self, task_id: str) -> SubagentStatus | None:
        """Return the current status of a subagent task, or None if unknown."""
        return self._task_statuses.get(task_id)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def list_running_statuses(self) -> list[SubagentStatus]:
        """Return statuses of all currently running subagents."""
        return [s for s in self._task_statuses.values() if s.phase not in ("done", "error")]

    # ------------------------------------------------------------------
    # Team communication: Worker ↔ Orchestrator
    # ------------------------------------------------------------------

    async def notify_orchestrator(
        self,
        message: str,
        worker_id: str,
        worker_label: str,
        priority: str = "info",
    ) -> str:
        """Fire-and-forget: publish a notification from Worker to Orchestrator."""
        if not self.bus:
            return "Error: message bus not available"
        origin = self._worker_origin.get(worker_id)
        if not origin:
            return "Error: worker origin not found"

        content = f"[Worker '{worker_label}' ({priority})]: {message}"
        wrapped = f"<system-reminder>\n{content}\n</system-reminder>"
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=wrapped,
            session_key_override=origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}",
            metadata={
                "injected_event": "worker_notification",
                "worker_id": worker_id,
                "worker_label": worker_label,
                "notification_priority": priority,
            },
        )
        await self.bus.publish_inbound(msg)
        logger.info("Worker [{}] notified Orchestrator (priority={}): {}", worker_label, priority, message[:80])
        return f"Orchestrator notified (priority: {priority})"

    def send_to_worker(self, worker_label: str, content: str) -> str:
        """Send a message from Orchestrator to a Worker. Fire-and-forget."""
        task_id = self._worker_label_to_id.get(worker_label)
        if task_id is None:
            known = list(self._worker_label_to_id.keys())
            return (
                f"Error: no worker with label '{worker_label}'. "
                f"Active workers: {known}"
            )
        # TOCTOU guard: check task is still running before accessing inbox
        task = self._running_tasks.get(task_id)
        inbox = self._worker_inboxes.get(task_id)
        if task is None or task.done() or inbox is None:
            return f"Error: worker '{worker_label}' has already completed. Message not delivered."
        inbox.put_nowait(f"[Orchestrator]: {content}")
        logger.info("Sent message to worker [{}]: {}", worker_label, content[:80])
        return f"Message sent to worker '{worker_label}'"

    async def request_orchestrator_input(
        self,
        question: str,
        worker_id: str,
        worker_label: str,
        context: str = "",
        timeout: float = 300.0,
    ) -> str:
        """Blocking: Worker asks Orchestrator for input, waits for response."""
        if not self.bus:
            return "Error: message bus not available"
        origin = self._worker_origin.get(worker_id)
        if not origin:
            return "Error: worker origin not found"

        # Create a Future and store it
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_worker_questions[worker_id] = future

        # Notify Orchestrator
        ctx = f"\nContext: {context}" if context else ""
        content = (
            f"[Worker '{worker_label}' requests input]: {question}{ctx}\n"
            f"Use respond_to_worker(worker_id='{worker_label}', response=...) to reply."
        )
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=content,
            session_key_override=origin.get("session_key"),
            metadata={
                "injected_event": "worker_request",
                "worker_id": worker_id,
                "worker_label": worker_label,
            },
        )
        await self.bus.publish_inbound(msg)
        logger.info("Worker [{}] requested Orchestrator input: {}", worker_label, question[:80])

        # Wait for response with timeout
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_worker_questions.pop(worker_id, None)
            logger.warning("Worker [{}] timed out waiting for Orchestrator input", worker_label)
            try:
                await self.notify_orchestrator(
                    f"Worker '{worker_label}' requested input but timed out after {timeout}s waiting for a response. "
                    f"Question was: {question[:200]}. Continuing autonomously.",
                    worker_id=worker_id,
                    worker_label=worker_label,
                    priority="warn",
                )
            except Exception:
                logger.debug("Failed to notify orchestrator of timeout", exc_info=True)
            return "Orchestrator did not respond in time. Continuing autonomously."
        except asyncio.CancelledError:
            self._pending_worker_questions.pop(worker_id, None)
            return "Request cancelled. Continuing autonomously."

    def respond_to_worker(self, worker_id: str, response: str) -> str:
        """Orchestrator responds to a Worker's pending request."""
        # Try worker_id as label first, then as task_id
        actual_id = self._worker_label_to_id.get(worker_id, worker_id)
        future = self._pending_worker_questions.pop(actual_id, None)
        if future is None:
            # Also try the original worker_id directly (could be task_id)
            future = self._pending_worker_questions.pop(worker_id, None)
        if future is None:
            known = list(self._pending_worker_questions.keys())
            labels = {v: k for k, v in self._worker_label_to_id.items()}
            known_labels = [labels.get(uid, uid) for uid in known]
            return (
                f"Error: no pending question from worker '{worker_id}'. "
                f"Workers with pending questions: {known_labels}"
            )
        future.set_result(response)
        logger.info("Orchestrator responded to worker [{}]: {}", worker_id, response[:80])
        return f"Response delivered to worker '{worker_id}'"
