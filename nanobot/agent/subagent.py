"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.memory_extractor import MemoryExtractor
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.context_vars import _in_subagent
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig, WebToolsConfig
from nanobot.providers.base import LLMProvider

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry

from .subagent_status import SubagentStatus, SubagentResult, format_error_progress
from .subagent_tools import build_subagent_tools
from .subagent_prompt import build_subagent_prompt


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(
        self, task_id: str, status: SubagentStatus | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._tools = tools
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

        # Inject conversation context into thinking tools that need it
        if self._tools is not None:
            for name in ("assess_me", "debug_root_cause"):
                tool = self._tools.get(name)
                if tool is not None:
                    sc = getattr(tool, "set_context", None)
                    if sc is not None:
                        sc(messages=list(context.messages))

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
        context_builder: Any | None = None,
        history_token_limit: int | None = None,
        compress_trigger_tokens: int | None = None,
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
        self._context_builder = context_builder
        self._history_token_limit = history_token_limit
        self._compress_trigger_tokens = compress_trigger_tokens
        # Concurrency cap: env var overrides default 5
        _max_sa = int(os.environ.get("NANOBOT_MAX_SUBAGENTS", "5"))
        self._spawn_semaphore = asyncio.Semaphore(max(1, _max_sa))
        self.runner = AgentRunner(provider, db=db)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._subagent_origin: dict[str, dict[str, str]] = {}  # task_id -> origin info
        self._subagent_label_to_id: dict[str, str] = {}  # label -> task_id
        self._subagent_inboxes: dict[str, "asyncio.Queue[str]"] = {}  # task_id -> inbox
        # Tracks sessions that ever spawned subagents (persists after cleanup)
        # so process_direct can detect the case where a subagent finished
        # during _process_message and cleanup already fired.
        self._session_spawned: set[str] = set()

    def was_spawned_in_session(self, session_key: str) -> bool:
        """Return True if any subagent was ever spawned in this session."""
        return session_key in self._session_spawned

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        role: str | None = None,
        context: str = "",
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        max_iterations: int | None = None,
        output_schema: str | None = None,
        max_timeout: int | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        # Deduplicate label if it already exists
        if display_label in self._subagent_label_to_id:
            suffix = 2
            while f"{display_label}_{suffix}" in self._subagent_label_to_id:
                suffix += 1
            display_label = f"{display_label}_{suffix}"

        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}
        self._subagent_origin[task_id] = origin
        self._subagent_label_to_id[display_label] = task_id
        self._subagent_inboxes[task_id] = asyncio.Queue()

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status
        if session_key:
            self._session_spawned.add(session_key)

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, status, context, max_iterations, output_schema, role=role, max_timeout=max_timeout)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            self._subagent_inboxes.pop(task_id, None)
            self._subagent_label_to_id.pop(display_label, None)
            self._subagent_origin.pop(task_id, None)
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
        role: str | None = None,
        max_timeout: int | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        effective_timeout = min(max_timeout or 3600, 7200)
        try:
            async with self._spawn_semaphore:
                tools = build_subagent_tools(self.workspace, self.web_config, self.exec_config, self.restrict_to_workspace, self._memory_store)

                # Register team communication tools
                from nanobot.agent.tools.notify_orchestrator import NotifyOrchestratorTool
                tools.register(NotifyOrchestratorTool(
                    manager=self, subagent_id=task_id, subagent_label=label,
                ))
                # Note: TellSubagentTool (main→subagent) is not registered here — it's main-agent only.
                system_prompt = build_subagent_prompt(
                    self.workspace,
                    self.disabled_skills,
                    timezone=getattr(self, 'timezone', None),
                    db=self.db,
                    tool_definitions=tools.get_definitions(),
                    project_root=self.project_root,
                    output_schema=output_schema,
                    role=role,
                    session_key=origin["session_key"],
                    label=label,
                )
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{task}\n\n{context}" if context.strip() else task},
                ]

                # Mark execution as subagent context — blocks nested spawn at tool level
                token = _in_subagent.set(True)
                try:
                    # Injection callback for main→subagent messages
                    inbox = self._subagent_inboxes.get(task_id)

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

                    from nanobot.agent.runner import AssessResult

                    async def _assess_callback(msgs: list[dict]) -> AssessResult:
                        """Full assess_me + debug_root_cause chain — same as main agent.

                        Runs periodic self-assessment, injects findings, and triggers
                        root-cause analysis when a blocker is detected.
                        """
                        from nanobot.agent.assess_me import (
                            assess_me as _run_sa,
                            build_assessment_message,
                            build_debug_root_cause_message,
                            is_assessment_message,
                            is_debug_root_cause_message,
                        )
                        try:
                            assess_text = await _run_sa(msgs, has_active_task=True)
                            if not assess_text:
                                return AssessResult()
                        except Exception:
                            logger.debug("Subagent [{}] periodic assess_me failed", task_id)
                            return AssessResult()

                        # Parse JSON output — handle <think> tags, code fences, trailing text
                        text = re.sub(r"<think>.*?</think>", "", assess_text, flags=re.DOTALL).strip()
                        text = re.sub(r"^```\w*\n?", "", text)
                        text = re.sub(r"\n?```$", "", text).strip()
                        start = text.find("{")
                        if start < 0:
                            logger.debug("Subagent [{}] assess_me non-JSON: {}…", task_id, assess_text[:100])
                            return AssessResult()
                        text = text[start:]
                        try:
                            decoder = json.JSONDecoder()
                            parsed, _ = decoder.raw_decode(text)
                        except Exception:
                            logger.debug("Subagent [{}] assess_me JSON parse failed", task_id)
                            return AssessResult()

                        status = parsed.get("status", "findings")
                        needs_revision = parsed.get("needs_revision") is True

                        if status == "ok" and not needs_revision:
                            logger.info("Subagent [{}] assess_me: all clear", task_id)
                            return AssessResult()

                        # Build injection text
                        parts = []
                        summary = parsed.get("summary", "")
                        if summary and summary != "null":
                            parts.append(f"## {summary}")
                        content = parsed.get("content", "")
                        if content and content != "null":
                            parts.append(content)
                        injection_text = "\n\n".join(parts) if parts else assess_text

                        if needs_revision:
                            injection_text += (
                                "\n\n以上评估发现需要修正的问题，请直接修正内容，"
                                "而非解释评估结果。如果修正涉及对话上下文中的信息，"
                                "请综合上下文完成修正。"
                            )

                        # Keep at most one assess_me message
                        for i in range(len(msgs) - 1, -1, -1):
                            if is_assessment_message(msgs[i]):
                                msgs.pop(i)
                        msgs.append(build_assessment_message(injection_text))
                        logger.info("Subagent [{}] periodic assess_me injected", task_id)

                        # DRC — blocker triggers root cause analysis
                        blocker = parsed.get("blocker")
                        if blocker and blocker != "null":
                            try:
                                from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
                                dcr = DebugRootCauseTool()
                                dcr.set_context(msgs)
                                dcr_result = await dcr.execute(problem=blocker)
                                if dcr_result and not dcr_result.startswith("Error:"):
                                    for i in range(len(msgs) - 1, -1, -1):
                                        if is_debug_root_cause_message(msgs[i]):
                                            msgs.pop(i)
                                    msgs.append(build_debug_root_cause_message(dcr_result))
                                    logger.info("Subagent [{}] debug_root_cause injected", task_id)
                            except Exception:
                                logger.debug("Subagent [{}] debug_root_cause failed", task_id)
                        else:
                            logger.debug("Subagent [{}] debug_root_cause skipped — no blocker", task_id)

                        return AssessResult(injected=True, needs_revision=needs_revision)

                    run_coro = self.runner.run(AgentRunSpec(
                        initial_messages=messages,
                        tools=tools,
                        model=self.model,
                        max_iterations=max_iterations or 100,
                        max_tool_result_chars=self.max_tool_result_chars,
                        hook=_SubagentHook(task_id, tools=tools, status=status),
                        max_iterations_message="任务已完成但未生成最终回复。",
                        error_message=None,
                        fail_on_tool_error=False,
                        concurrent_tools=True,
                        checkpoint_callback=_on_checkpoint,
                        injection_callback=_drain_inbox,
                        reasoning_effort=self.runner.provider.generation.reasoning_effort,
                        session_key=origin["session_key"],
                        instructions=lambda: self._context_builder.build_instructions_section(for_subagent=True, session_key=origin["session_key"], tool_instruction_map=tools.get_instruction_map()) if self._context_builder else None,
                        history_token_limit=self._history_token_limit,
                        compress_trigger_tokens=self._compress_trigger_tokens,
                        assess_me_callback=_assess_callback,
                        assess_interval=10,
                        keyword_search_callback=self._memory_store.vector_index.search if self._memory_store else None,
                    ))
                    result = await asyncio.wait_for(run_coro, timeout=effective_timeout)

                    # Self-assessment — run assess_me on completed conversation
                    assessment: str | None = None
                    try:
                        from nanobot.agent.assess_me import assess_me as _run_self_assess
                        assess_text = await _run_self_assess(result.messages, has_active_task=True)
                        if assess_text:
                            assessment = assess_text
                            stripped = re.sub(r"<think>.*?</think>", "", assess_text, flags=re.DOTALL).strip()
                            logger.info("Subagent [{}] self-assessment: {}", task_id, stripped[:200])
                    except Exception:
                        logger.debug("Subagent [{}] self-assessment skipped", task_id)

                    # Assess-verify: check for unresolved gaps/blockers
                    needs_review = False
                    if assessment:
                        low = assessment.lower()
                        if any(kw in low for kw in ("盲点", "未验证", "blocker", "信息不足", "gap", "假设未验证", "findings")):
                            needs_review = True
                            logger.info("Subagent [{}] assessment flagged issues, marking needs_review", task_id)

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

                    eff_status = "needs_review" if needs_review else \
                        ("ok" if result.stop_reason in ("completed", "stop", "empty_final_response", "tool_loop_breaker") else "error")

                    sub_result = SubagentResult(
                        task_id=task_id,
                        label=label,
                        status=eff_status,
                        final_content=result.final_content,
                        tools_used=list(result.tools_used),
                        duration_s=duration_s,
                        iteration_count=status.iteration,
                        token_usage=token_usage,
                        errors=[result.error] if result.error else [],
                        output_schema=output_schema,
                        assessment=assessment,
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
                        announce_status = "needs_review" if needs_review else \
                            ("error" if sub_result.status == "error" else "ok")
                        if announce_status == "ok":
                            logger.info("Subagent [{}] completed successfully", task_id)
                        elif announce_status == "needs_review":
                            logger.warning("Subagent [{}] completed but assessment flagged issues", task_id)
                        else:
                            logger.warning("Subagent [{}] stopped with status {}", task_id, result.stop_reason)
                        await self._announce_result(task_id, label, task, final_result, origin, announce_status, sub_result=sub_result, pt_path=pt_path)
                finally:
                    _in_subagent.reset(token)

        except asyncio.TimeoutError:
            status.phase = "timeout"
            timeout_s = effective_timeout
            elapsed_s = int(time.monotonic() - status.started_at)
            last_tools = [e["name"] for e in status.tool_events[-3:]]
            logger.warning(
                "Subagent [{}] timed out after {}s ({} iterations, phase={})",
                task_id, timeout_s, status.iteration, status.phase,
            )
            partial_result = SubagentResult(
                task_id=task_id,
                label=label,
                status="error",
                final_content=None,
                tools_used=list({e["name"] for e in status.tool_events}),
                duration_s=elapsed_s,
                iteration_count=status.iteration,
                token_usage=dict(status.usage),
                errors=[f"Timeout after {timeout_s}s"],
                output_schema=output_schema,
            )
            msg = (
                f"Subagent timed out after {timeout_s}s. "
                f"Completed {status.iteration} iterations in phase '{status.phase}'."
            )
            if last_tools:
                msg += f"\nLast tools: {', '.join(last_tools)}"
            msg += (
                "\n\n以上为超时前的部分结果。如需继续处理剩余部分请重新分拆任务，"
                "或基于已有结果继续推进。"
            )
            await self._announce_result(
                task_id, label, task, msg, origin, "error",
                sub_result=partial_result,
            )
        except asyncio.CancelledError:
            status.phase = "cancelled"
            logger.warning("Subagent [{}] cancelled by orchestrator", task_id)
            await self._announce_result(task_id, label, task, "Subagent cancelled by orchestrator", origin, "error")
            raise
        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error")

    async def _inject_to_orchestrator(
        self,
        content: str,
        origin: dict[str, str],
        *,
        sender_id: str = "user",
        metadata: dict[str, Any] | None = None,
        ephemeral: bool = False,
    ) -> None:
        """Unified injection: deliver a message to the Orchestrator via the message bus.

        All Subagent → Orchestrator messages go through this method to ensure
        consistent routing, session key handling, and metadata.

        - sender_id is always "user" so all bus messages present as user-originated
        - _origin_channel/_origin_chat_id identify the subagent message for handler
        - session_key_override resolves with fallback to origin channel:chat_id
        """
        session_key = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        msg = InboundMessage(
            channel="system",
            sender_id=sender_id,
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=content,
            session_key_override=session_key,
            metadata={
                "_origin_channel": origin.get("channel", ""),
                "_origin_chat_id": origin.get("chat_id", ""),
                **(metadata or {}),
            },
            ephemeral=ephemeral,
        )
        await self.bus.publish_inbound(msg)

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

        status_text = "completed successfully" if status == "ok" else "completed with issues — needs review" if status == "needs_review" else "failed"
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
            assessment=sub_result.assessment if sub_result and sub_result.assessment else "",
        )

        await self._inject_to_orchestrator(
            announce_content,
            origin,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
            },
        )
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

    async def cancel_by_label(self, label: str) -> str:
        """Cancel a specific subagent by its subagent label."""
        task_id = self._subagent_label_to_id.get(label)
        if task_id is None:
            known = list(self._subagent_label_to_id.keys())
            return f"Error: no Subagent with label '{label}'. Active Subagents: {known}"
        task = self._running_tasks.get(task_id)
        if task is None or task.done():
            return f"Subagent '{label}' has already completed."
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected error awaiting cancelled subagent task")
        return f"Subagent '{label}' cancelled."

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

    def get_sessions_with_running_subagents(self) -> list[str]:
        """Return session keys that have at least one running Subagent."""
        return [
            sk for sk, tids in self._session_tasks.items()
            if any(tid in self._running_tasks and not self._running_tasks[tid].done() for tid in tids)
        ]

    def list_running_statuses(self) -> list[SubagentStatus]:
        """Return statuses of all currently running subagents."""
        return [s for s in self._task_statuses.values() if s.phase not in ("done", "error")]

    # ------------------------------------------------------------------
    # Team communication: Subagent ↔ Orchestrator
    # ------------------------------------------------------------------

    async def notify_orchestrator(
        self,
        message: str,
        subagent_id: str,
        subagent_label: str,
        priority: str = "info",
    ) -> str:
        """Fire-and-forget: publish a notification from Subagent to Orchestrator."""
        if not self.bus:
            return "Error: message bus not available"
        origin = self._subagent_origin.get(subagent_id)
        if not origin:
            return "Error: Subagent origin not found"

        await self._inject_to_orchestrator(
            f"[Subagent '{subagent_label}' ({priority})]: {message}",
            origin,
            metadata={
                "injected_event": "subagent_notification",
                "subagent_id": subagent_id,
                "subagent_label": subagent_label,
                "notification_priority": priority,
            },
        )
        logger.info("Subagent [{}] notified Orchestrator (priority={}): {}", subagent_label, priority, message[:80])
        return f"Orchestrator notified (priority: {priority})"

    def send_to_subagent(self, subagent_label: str, content: str) -> str:
        """Send a message from Orchestrator to a Subagent. Fire-and-forget."""
        task_id = self._subagent_label_to_id.get(subagent_label)
        if task_id is None:
            known = list(self._subagent_label_to_id.keys())
            return (
                f"Error: no Subagent with label '{subagent_label}'. "
                f"Active Subagents: {known}"
            )
        # TOCTOU guard: check task is still running before accessing inbox
        task = self._running_tasks.get(task_id)
        inbox = self._subagent_inboxes.get(task_id)
        if task is None or task.done() or inbox is None:
            return f"Error: Subagent '{subagent_label}' has already completed. Message not delivered."
        inbox.put_nowait(f"[Orchestrator]: {content}")
        logger.info("Sent message to Subagent [{}]: {}", subagent_label, content[:80])
        return f"Message sent to Subagent '{subagent_label}'"

