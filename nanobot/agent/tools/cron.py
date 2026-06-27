"""Cron tool for scheduling reminders and tasks."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJobState, CronSchedule

_CRON_PARAMETERS = build_parameters_schema(
    action=p("string", "Action to perform", enum=["add", "list", "remove", "update", "test"]),
    name=p("string",
        "Optional short human-readable label for the job "
        "(e.g., 'weather-monitor', 'daily-standup')."
    ),
    message=p("string",
        "REQUIRED when action='add'. Instruction for the agent to execute when the job triggers "
        "(e.g., 'Send a reminder to WeChat: xxx' or 'Check system status and report'). "
        "Not used for action='list' or action='remove'.",
        minLength=1,
    ),
    every_seconds=p("integer", "Interval in seconds (for recurring tasks)"),
    cron_expr=p("string", "Cron expression like '0 9 * * *' (for scheduled tasks)"),
    tz=p("string",
        "Optional IANA timezone for cron expressions (e.g. 'America/Vancouver'). "
        "Defaults to 'UTC' if omitted."
    ),
    at=p("string",
        "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00'). "
        "Naive values use UTC."
    ),
    deliver=p("boolean", "Whether to deliver the execution result to the user channel (default true)",
        default=True,
    ),
    job_id=p("string", "REQUIRED for action='remove', 'update', or 'test'. "
        "Optional when inside a cron job (defaults to current job). "
        "Obtain via action='list'.",
        minLength=1,
    ),
    dry_run=p("boolean",
        "For action='test': run without delivering result to user channel. "
        "Use this to test the task without sending messages.",
        default=False,
    ),
    required=["action"],
    description=(
        "Schedule timed/recurring tasks. "
        "当用户说'每天早上X点'、'每X小时'、'定期'、'定时'时，使用此工具安排任务。\n\n"
        "Actions: add (needs message + schedule), remove (needs job_id), "
        "update, list, test. "
        "Schedule options: every_N_seconds, cron_expr, or at (ISO 8601 time).\n\n"
        "Do NOT use glob or grep for scheduling — use this tool."
    ),
)


@tool_parameters(_CRON_PARAMETERS)
class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""

    def __init__(self, cron_service: CronService, default_timezone: str = "UTC"):
        self._cron = cron_service
        self._default_timezone = default_timezone
        self._channel: ContextVar[str] = ContextVar("cron_channel", default="")
        self._chat_id: ContextVar[str] = ContextVar("cron_chat_id", default="")
        self._metadata: ContextVar[dict] = ContextVar("cron_metadata", default={})
        self._session_key: ContextVar[str] = ContextVar("cron_session_key", default="")
        self._in_cron_context: ContextVar[bool] = ContextVar("cron_in_context", default=False)
        self._current_job_id: ContextVar[str] = ContextVar("cron_job_id", default="")
        self._test_mode: ContextVar[bool] = ContextVar("cron_test_mode", default=False)
        self._dry_run: ContextVar[bool] = ContextVar("cron_dry_run", default=False)
        self._progress_callback: ContextVar[callable | None] = ContextVar("cron_progress_cb", default=None)
        self._execution_log: ContextVar[list[str]] = ContextVar("cron_exec_log", default=[])

    def set_context(
        self, channel: str, chat_id: str,
        metadata: dict | None = None, session_key: str | None = None,
    ) -> None:
        """Set the current session context for delivery."""
        self._channel.set(channel)
        self._chat_id.set(chat_id)
        self._metadata.set(metadata or {})
        self._session_key.set(session_key or f"{channel}:{chat_id}")

    def set_cron_context(self, active: bool, dry_run: bool = False):
        """Mark whether the tool is executing inside a cron job callback."""
        self._in_cron_context.set(active)
        self._dry_run.set(dry_run)

    def reset_cron_context(self, token) -> None:
        """Restore previous cron context."""
        self._in_cron_context.reset(token)

    def set_current_job_id(self, job_id: str):
        """Set the current cron job ID for self-referencing update/remove."""
        return self._current_job_id.set(job_id)

    def reset_current_job_id(self, token) -> None:
        """Restore previous current job ID."""
        self._current_job_id.reset(token)

    def get_execution_log(self) -> list[str]:
        """Get the current execution log for test/debug display."""
        return self._execution_log.get()

    def clear_execution_log(self) -> None:
        """Clear the current execution log."""
        self._execution_log.set(list())

    def set_progress_callback(self, cb: callable | None) -> None:
        """Set callback for progress updates during cron execution."""
        self._progress_callback.set(cb)

    def reset_progress_callback(self, token) -> None:
        """Restore previous progress callback."""
        self._progress_callback.reset(token)

    @staticmethod
    def _validate_timezone(tz: str) -> str | None:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(tz)
        except (KeyError, Exception):
            logger.warning("Invalid timezone: {}", tz)
            return f"Error: unknown timezone '{tz}'"
        return None

    def _display_timezone(self, schedule: CronSchedule) -> str:
        """Pick the most human-meaningful timezone for display."""
        return schedule.tz or self._default_timezone

    @staticmethod
    def _format_timestamp(ms: int, tz_name: str) -> str:
        from zoneinfo import ZoneInfo
        from nanobot.utils.helpers import _format_datetime

        dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))
        return _format_datetime(dt)
    instruction = "Schedule timed/recurring tasks. Trigger phrases: '每天早上X点', '每X小时', '定期', '定时'. Call this tool directly — do NOT search for how to schedule."

    name = "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule timed/recurring tasks. Supports three modes: "
            "every_seconds (interval), cron_expr (cron expression), at (one-time). "
            "Actions: add, remove, update, list, test. "
            "tz parameter works only with cron_expr. "
            "System tasks (e.g., extractor) cannot be deleted/modified."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        action = params.get("action")
        if action == "add" and not str(params.get("message") or "").strip():
            errors.append("message is required when action='add'")
        if action in ("remove", "update", "test") and not str(params.get("job_id") or "").strip():
            if not self._in_cron_context.get():
                errors.append(f"job_id is required when action='{action}' (or run inside a cron job)")
        return errors

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        deliver: bool = True,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(name, message, every_seconds, cron_expr, tz, at, deliver)
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        elif action == "update":
            return self._update_job(job_id, name, message, every_seconds, cron_expr, tz, at, deliver)
        elif action == "test":
            return await self._test_job(job_id, dry_run)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        deliver: bool = True,
    ) -> str:
        if not message:
            return (
                "Error: cron action='add' requires a non-empty 'message' parameter "
                "describing what to do when the job triggers "
                "(e.g. the reminder text). Retry including message=\"...\"."
            )
        channel = self._channel.get()
        chat_id = self._chat_id.get()
        if not channel or not chat_id:
            return "Error: no session context (channel/chat_id)"
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            if err := self._validate_timezone(tz):
                return err

        # Build schedule
        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            effective_tz = tz or self._default_timezone
            if err := self._validate_timezone(effective_tz):
                return err
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=effective_tz)
        elif at:
            from zoneinfo import ZoneInfo

            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime format '{at}'. Expected format: YYYY-MM-DDTHH:MM:SS"
            if dt.tzinfo is None:
                if err := self._validate_timezone(self._default_timezone):
                    return err
                dt = dt.replace(tzinfo=ZoneInfo(self._default_timezone))
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=chat_id,
            delete_after_run=delete_after,
            channel_meta=self._metadata.get(),
            session_key=self._session_key.get() or None,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _format_timing(self, schedule: CronSchedule) -> str:
        """Format schedule as a human-readable timing string."""
        if schedule.kind == "cron":
            tz = f" ({schedule.tz})" if schedule.tz else ""
            return f"cron: {schedule.expr}{tz}"
        if schedule.kind == "every" and schedule.every_ms:
            ms = schedule.every_ms
            if ms % 3_600_000 == 0:
                return f"every {ms // 3_600_000}h"
            if ms % 60_000 == 0:
                return f"every {ms // 60_000}m"
            if ms % 1000 == 0:
                return f"every {ms // 1000}s"
            return f"every {ms}ms"
        if schedule.kind == "at" and schedule.at_ms:
            return f"at {self._format_timestamp(schedule.at_ms, self._display_timezone(schedule))}"
        return schedule.kind

    def _format_state(self, state: CronJobState, schedule: CronSchedule) -> list[str]:
        """Format job run state as display lines."""
        lines: list[str] = []
        display_tz = self._display_timezone(schedule)
        if state.last_run_at_ms:
            info = (
                f"  Last run: {self._format_timestamp(state.last_run_at_ms, display_tz)}"
                f" — {state.last_status or 'unknown'}"
            )
            if state.last_error:
                info += f" ({state.last_error})"
            lines.append(info)
        if state.next_run_at_ms:
            lines.append(f"  Next run: {self._format_timestamp(state.next_run_at_ms, display_tz)}")
        return lines

    @staticmethod
    def _system_job_purpose() -> str:
        return "System-managed internal job."

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for j in jobs:
            timing = self._format_timing(j.schedule)
            parts = [f"- {j.name} (id: {j.id}, {timing})"]
            if j.payload.kind == "system_event":
                parts.append(f"  Purpose: {self._system_job_purpose()}")
                parts.append("  Protected: visible for inspection, but cannot be removed.")
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        result = self._cron.remove_job(job_id)
        if result == "removed":
            return f"Removed job {job_id}"
        if result == "protected":
            job = self._cron.get_job(job_id)
            if job:
                return (
                    "Cannot remove system job.\n"
                    "It remains visible so you can inspect it, but it cannot be removed."
                )
            return (
                f"Cannot remove job `{job_id}`.\n"
                "This is a protected system-managed cron job."
            )
        return f"Job {job_id} not found"

    def _update_job(
        self,
        job_id: str | None,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        deliver: bool = True,
    ) -> str:
        if not job_id:
            job_id = self._current_job_id.get()
        if not job_id:
            return "Error: job_id is required for update (use job_id=... or run inside a cron job)"

        # Build schedule if any scheduling param is provided
        schedule = None
        if every_seconds or cron_expr or at:
            if every_seconds:
                schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
            elif cron_expr:
                effective_tz = tz or self._default_timezone
                if err := self._validate_timezone(effective_tz):
                    return err
                schedule = CronSchedule(kind="cron", expr=cron_expr, tz=effective_tz)
            elif at:
                from zoneinfo import ZoneInfo
                try:
                    dt = datetime.fromisoformat(at)
                except ValueError:
                    return f"Error: invalid ISO datetime format '{at}'. Expected format: YYYY-MM-DDTHH:MM:SS"
                if dt.tzinfo is None:
                    if err := self._validate_timezone(self._default_timezone):
                        return err
                    dt = dt.replace(tzinfo=ZoneInfo(self._default_timezone))
                at_ms = int(dt.timestamp() * 1000)
                schedule = CronSchedule(kind="at", at_ms=at_ms)

        # Only pass non-default values to update
        result = self._cron.update_job(
            job_id,
            name=name,
            schedule=schedule,
            message=message or None,
            deliver=None if deliver else False,
        )

        if result == "not_found":
            return f"Error: job '{job_id}' not found"
        if result == "protected":
            return f"Error: job '{job_id}' is a protected system job and cannot be updated"

        updated = result
        parts = [f"Updated job '{updated.name}' ({updated.id})"]
        if schedule:
            parts.append(f"  New schedule: {self._format_timing(schedule)}")
        if message:
            parts.append(f"  New message: {message[:60]}{'...' if len(message) > 60 else ''}")
        return "\n".join(parts)

    async def _test_job(self, job_id: str | None, dry_run: bool = False) -> str:
        """Test run a cron job immediately for debugging.

        Features:
        - Shows execution steps in real-time via on_progress callback
        - Supports dry_run mode (execute without delivering to user)
        - Returns detailed result or error message
        """
        if not job_id:
            job_id = self._current_job_id.get()
        if not job_id:
            return "Error: job_id is required for test (or run inside a cron job)"

        job = self._cron.get_job(job_id)
        if not job:
            return f"Error: job '{job_id}' not found"

        if not self._cron.on_job:
            return "Error: cron service has no on_job handler (test not available in this context)"

        steps = []

        def on_progress(step: str) -> None:
            """Collect execution steps for display."""
            steps.append(f"  [Step] {step}")

        try:
            # Mark test mode and dry_run
            test_token = self._test_mode.set(True)
            dry_run_token = self._dry_run.set(dry_run)
            job_token = self.set_current_job_id(job.id)

            # Set the progress callback
            old_callback = self._progress_callback.get()
            self._progress_callback.set(on_progress)

            steps.append(f"Test running job '{job.name}' (id: {job.id})")
            if dry_run:
                steps.append("  [Mode] Dry run - result will not be delivered")

            # Save original deliver for restoration after test
            original_deliver = getattr(job.payload, "deliver", True)
            if dry_run:
                try:
                    if hasattr(job.payload, "deliver"):
                        job.payload.deliver = False
                except Exception:
                    pass  # ignore errors, job will run anyway

            try:
                # Execute the job
                result = await self._cron.run_job(job_id, force=True)

                steps.append("")  # blank line before result
                if isinstance(result, str):
                    steps.append("✅ Test completed successfully")
                    steps.append(f"Result preview: {result[:200]}{'...' if len(result) > 200 else ''}")
                elif result:
                    steps.append("✅ Test completed successfully")
                else:
                    steps.append("⚠️  Test completed but returned empty result")

                return "\n".join(steps)

            finally:
                # Restore original deliver value if it was changed for dry run
                if dry_run and original_deliver is not None:
                    try:
                        if hasattr(job.payload, "deliver"):
                            job.payload.deliver = original_deliver
                    except Exception:
                        pass
                self._progress_callback.set(old_callback)
                self._test_mode.reset(test_token)
                self._dry_run.reset(dry_run_token)
                self.reset_current_job_id(job_token)

        except Exception as e:
            logger.exception("Cron test failed for job '{}'", job_id)
            steps.append(f"❌ Test run failed: {e}")
            return "\n".join(steps)