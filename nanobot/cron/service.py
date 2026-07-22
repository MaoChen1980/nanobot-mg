"""Cron service for scheduling agent tasks."""

import asyncio
import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal, Optional, Union

from filelock import FileLock
from loguru import logger

from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronRunRecord, CronSchedule, CronStore


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> Optional[int]:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception as e:
            logger.warning("Failed to compute next cron trigger time: {}", e)
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    """Service for managing and executing scheduled jobs."""

    _MAX_RUN_HISTORY = 20

    def __init__(
        self,
        store_path: Path,
        on_job: Optional[Callable[[CronJob], Coroutine[Any, Any, Optional[str]]]] = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        self.store_path = store_path
        self._action_path = store_path.parent / "action.jsonl"
        self._lock = FileLock(str(self._action_path.parent) + ".lock")
        self.on_job = on_job
        self._store: Optional[CronStore] = None
        self._timer_task: Optional[asyncio.Task] = None
        self._running = False
        self._timer_active = False
        self.max_sleep_ms = max_sleep_ms

    def _load_jobs(self) -> tuple[list[CronJob], int] | None:
        """Load jobs from disk.

        Returns:
            ``(jobs, version)`` tuple on success or when no store file exists
            (in which case an empty list and version 1 are returned).
            ``None`` when the store file exists but cannot be parsed; the
            corrupt file is preserved with a ``.corrupt-<ts>`` suffix so the
            caller can decide whether to overwrite or bail out.  Returning a
            sentinel here is important: silently treating a parse error as an
            empty job list would cause the next ``_save_store`` to wipe every
            job from disk.
        """
        if not self.store_path.exists():
            return [], 1
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            jobs: list[CronJob] = []
            version = data.get("version", 1)
            for j in data.get("jobs", []):
                jobs.append(CronJob(
                    id=j["id"],
                    name=j["name"],
                    enabled=j.get("enabled", True),
                    schedule=CronSchedule(
                        kind=j["schedule"]["kind"],
                        at_ms=j["schedule"].get("atMs"),
                        every_ms=j["schedule"].get("everyMs"),
                        expr=j["schedule"].get("expr"),
                        tz=j["schedule"].get("tz"),
                    ),
                    payload=CronPayload(
                        kind=j["payload"].get("kind", "agent_turn"),
                        message=j["payload"].get("message", ""),
                        deliver=j["payload"].get("deliver", False),
                        channel=j["payload"].get("channel"),
                        to=j["payload"].get("to"),
                        channel_meta=(
                            j["payload"].get("channelMeta")
                            or j["payload"].get("channel_meta")
                            or {}
                        ),
                        session_key=j["payload"].get("sessionKey") or j["payload"].get("session_key"),
                        policy=j["payload"].get("policy", "queue"),
                    ),
                    state=CronJobState(
                        next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                        last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                        last_status=j.get("state", {}).get("lastStatus"),
                        last_error=j.get("state", {}).get("lastError"),
                        run_history=[
                            CronRunRecord(
                                run_at_ms=r["runAtMs"],
                                status=r["status"],
                                duration_ms=r.get("durationMs", 0),
                                error=r.get("error"),
                            )
                            for r in j.get("state", {}).get("runHistory", [])
                        ],
                    ),
                    created_at_ms=j.get("createdAtMs", 0),
                    updated_at_ms=j.get("updatedAtMs", 0),
                    delete_after_run=j.get("deleteAfterRun", False),
                ))
        except Exception:
            backup = self.store_path.with_suffix(
                self.store_path.suffix + f".corrupt-{int(time.time())}"
            )
            with suppress(OSError):
                self.store_path.rename(backup)
            logger.exception(
                "Failed to load cron store at {}. "
                "Corrupt file preserved at {}. "
                "Refusing to overwrite to avoid data loss.",
                self.store_path,
                backup,
            )
            return None
        return jobs, version

    def _merge_action(self):
        if not self._action_path.exists():
            return

        jobs_map = {j.id: j for j in self._store.jobs}
        def _update(params: dict):
            j = CronJob.from_dict(params)
            jobs_map[j.id] = j

        def _del(params: dict):
            if job_id := params.get("job_id"):
                jobs_map.pop(job_id)

        with self._lock:
            with open(self._action_path, "r", encoding="utf-8") as f:
                changed = False
                for line in f:
                    try:
                        line = line.strip()
                        action = json.loads(line)
                        if "action" not in action:
                            continue
                        if action["action"] == "del":
                            _del(action.get("params", {}))
                        else:
                            _update(action.get("params", {}))
                        changed = True
                    except Exception as exp:
                        logger.warning("Failed to load cron action line: {}", exp)
                        continue
            self._store.jobs = list(jobs_map.values())
            if self._running and changed:
                self._action_path.write_text("", encoding="utf-8")
                self._save_store()
        return

    def _load_store(self) -> CronStore | None:
        """Load jobs from disk. Reloads automatically if file was modified externally.
        - Reload every time because it needs to merge operations on the jobs object from other instances.
        - During _on_timer execution, return the existing store to prevent concurrent
          _load_store calls (e.g. from list_jobs polling) from replacing it mid-execution.
        - When the on-disk store exists but is unreadable: keep using the
          previous in-memory ``self._store`` if we already have one (so a
          transient corruption does not drop live jobs); only the very first
          load (during ``start``) can return ``None`` to signal an unrecoverable
          state to the caller.
        """
        if self._timer_active and self._store:
            return self._store
        loaded = self._load_jobs()
        if loaded is None:
            if self._store is not None:
                return self._store
            return None
        jobs, version = loaded
        self._store = CronStore(version=version, jobs=jobs)
        self._merge_action()

        return self._store

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write *content* to *path* atomically with fsync.

        Uses a temp-file + ``os.replace`` + ``fsync`` pattern so a crash or
        SIGKILL mid-write cannot leave the destination truncated or invalid.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            with suppress(PermissionError):
                fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                        "channelMeta": j.payload.channel_meta,
                        "sessionKey": j.payload.session_key,
                        "policy": j.payload.policy,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self._atomic_write(self.store_path, json.dumps(data, indent=2, ensure_ascii=False))

    def _replay_action_queue(self) -> None:
        """Replay action queue from when service was stopped.

        When the service is not running, add_job/remove_job/update_job record their
        operations to action.jsonl via _append_action(). On restart, these are
        replayed so no operations are lost.
        """
        if not self._action_path.exists():
            return
        try:
            lines = self._action_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return
        if not lines:
            return
        replayed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = entry.get("action")
            params = entry.get("params", {})
            if action == "add":
                # Check if already exists (idempotent)
                job_dict = params
                job_id = job_dict.get("id")
                if job_id and any(j.id == job_id for j in self._store.jobs):
                    continue
                try:
                    job = CronJob.from_dict(job_dict)
                    self._store.jobs.append(job)
                    replayed += 1
                except Exception:
                    logger.warning("Cron: failed to replay 'add' action for job {}", job_id)
            elif action == "del":
                job_id = params.get("job_id")
                if not job_id:
                    continue
                before = len(self._store.jobs)
                self._store.jobs = [j for j in self._store.jobs if j.id != job_id]
                if len(self._store.jobs) < before:
                    replayed += 1
            elif action == "update":
                job_id = params.get("id")
                if not job_id:
                    continue
                idx = next((i for i, j in enumerate(self._store.jobs) if j.id == job_id), None)
                if idx is not None:
                    try:
                        updated = CronJob.from_dict(params)
                        self._store.jobs[idx] = updated
                        replayed += 1
                    except Exception:
                        logger.warning("Cron: failed to replay 'update' action for job {}", job_id)
        if replayed:
            self._save_store()
            logger.info("Cron: replayed {} action(s) from queue", replayed)
        # Clear the queue after replay
        try:
            self._action_path.write_text("", encoding="utf-8")
        except Exception:
            pass  # Non-fatal if we can't clear

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        loaded = self._load_store()
        if loaded is None:
            self._running = False
            raise RuntimeError(
                f"cron store at {self.store_path} is corrupt and was preserved; "
                "refusing to start with an empty job list. "
                "Inspect the .corrupt-<ts> backup and restore manually."
            )
        # Replay queued actions from when service was stopped (add/del/update ops)
        self._replay_action_queue()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> Optional[int]:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick."""
        if self._timer_task:
            self._timer_task.cancel()

        if not self._running:
            return

        next_wake = self._get_next_wake_ms()
        if next_wake is None:
            delay_ms = self.max_sleep_ms
        else:
            delay_ms = min(self.max_sleep_ms, max(0, next_wake - _now_ms()))
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        self._load_store()
        if not self._store:
            self._arm_timer()
            return

        self._timer_active = True
        try:
            now = _now_ms()

            # Clean up stale one-shot jobs that were never picked up
            # (e.g. service started after at_ms had passed).
            # Use a 60s grace period to avoid removing jobs that just became due.
            stale = [
                j for j in self._store.jobs
                if j.enabled and j.schedule.kind == "at" and j.delete_after_run
                and j.state.next_run_at_ms is None
                and j.schedule.at_ms and now - j.schedule.at_ms > 60_000
            ]
            if stale:
                stale_desc = ", ".join(f"'{j.name}' ({j.id})" for j in stale)
                stale_ids = {j.id for j in stale}
                self._store.jobs = [j for j in self._store.jobs if j.id not in stale_ids]
                logger.info("Cron: removed stale one-shot job(s): {}", stale_desc)

            due_jobs = [
                j for j in self._store.jobs
                if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
            ]

            for job in due_jobs:
                await self._execute_job(job)

            self._save_store()
        finally:
            self._timer_active = False
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job with retry for transient failures."""
        max_retries = 3
        base_delay_s = 5
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        for attempt in range(max_retries):
            try:
                if self.on_job:
                    await self.on_job(job)

                job.state.last_status = "ok"
                job.state.last_error = None
                logger.info("Cron: job '{}' completed", job.name)
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = base_delay_s * (2 ** attempt)
                    logger.warning(
                        "Cron: job '{}' failed (attempt {}/{}), retrying in {}s: {}",
                        job.name, attempt + 1, max_retries, wait, e,
                    )
                    await asyncio.sleep(wait)
                    continue

                job.state.last_status = "error"
                job.state.last_error = str(e)
                logger.error("Cron: job '{}' failed after {} attempts: {}", job.name, max_retries, e)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        job.state.run_history.append(CronRunRecord(
            run_at_ms=start_ms,
            status=job.state.last_status,
            duration_ms=end_ms - start_ms,
            error=job.state.last_error,
        ))
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY:]

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    def _append_action(self, action: Literal["add", "del", "update"], params: dict):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._action_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "params": params}, ensure_ascii=False) + "\n")


    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        if store is None:
            return []
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: Optional[str] = None,
        to: Optional[str] = None,
        delete_after_run: bool = False,
        channel_meta: Optional[dict] = None,
        session_key: Optional[str] = None,
        policy: Literal["queue", "idle", "interrupt"] = "queue",
    ) -> CronJob:
        """Add a new job."""
        _validate_schedule_for_add(schedule)
        now = _now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind="agent_turn",
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
                channel_meta=channel_meta or {},
                session_key=session_key,
                policy=policy,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        if self._running:
            store = self._load_store()
            store.jobs.append(job)
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("add", asdict(job))

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def register_system_job(self, job: CronJob) -> CronJob:
        """Register an internal system job (idempotent on restart)."""
        store = self._load_store()
        if store is None:
            store = CronStore(version=1, jobs=[])
            self._store = store
        now = _now_ms()
        job.state = CronJobState(next_run_at_ms=_compute_next_run(job.schedule, now))
        job.created_at_ms = now
        job.updated_at_ms = now
        store.jobs = [j for j in store.jobs if j.id != job.id]
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        logger.info("Cron: registered system job '{}' ({})", job.name, job.id)
        return job

    def remove_job(self, job_id: str) -> Literal["removed", "protected", "not_found"]:
        """Remove a job by ID, unless it is a protected system job."""
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            logger.info("Cron: refused to remove protected system job {}", job_id)
            return "protected"

        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            if self._running:
                self._save_store()
                self._arm_timer()
            else:
                self._append_action("del", {"job_id": job_id})
            logger.info("Cron: removed job {}", job_id)
            return "removed"

        return "not_found"

    def enable_job(self, job_id: str, enabled: bool = True) -> Optional[CronJob]:
        """Enable or disable a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                job.enabled = enabled
                job.updated_at_ms = _now_ms()
                if enabled:
                    job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
                else:
                    job.state.next_run_at_ms = None
                if self._running:
                    self._save_store()
                    self._arm_timer()
                else:
                    self._append_action("update", asdict(job))
                return job
        return None

    def update_job(
        self,
        job_id: str,
        *,
        name: Optional[str] = None,
        schedule: Optional[CronSchedule] = None,
        message: Optional[str] = None,
        deliver: Optional[bool] = None,
        channel: Optional[str] = ...,
        to: Optional[str] = ...,
        delete_after_run: Optional[bool] = None,
    ) -> Union[CronJob, Literal["not_found", "protected"]]:
        """Update mutable fields of an existing job. System jobs cannot be updated.

        For ``channel`` and ``to``, pass an explicit value (including ``None``)
        to update; omit (sentinel ``...``) to leave unchanged.
        """
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            return "protected"

        if schedule is not None:
            _validate_schedule_for_add(schedule)
            job.schedule = schedule
        if name is not None:
            job.name = name
        if message is not None:
            job.payload.message = message
        if deliver is not None:
            job.payload.deliver = deliver
        if channel is not ...:
            job.payload.channel = channel
        if to is not ...:
            job.payload.to = to
        if delete_after_run is not None:
            job.delete_after_run = delete_after_run

        job.updated_at_ms = _now_ms()
        if job.enabled:
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

        if self._running:
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("update", asdict(job))

        logger.info("Cron: updated job '{}' ({})", job.name, job.id)
        return job

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job without disturbing the service's running state."""
        was_running = self._running
        self._running = True
        try:
            store = self._load_store()
            for job in store.jobs:
                if job.id == job_id:
                    if not force and not job.enabled:
                        return False
                    await self._execute_job(job)
                    self._save_store()
                    return True
            return False
        finally:
            self._running = was_running
            if was_running:
                self._arm_timer()

    def get_job(self, job_id: str) -> Optional[CronJob]:
        """Get a job by ID."""
        store = self._load_store()
        return next((j for j in store.jobs if j.id == job_id), None)

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
