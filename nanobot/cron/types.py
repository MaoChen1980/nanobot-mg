"""Cron types."""

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: Optional[int] = None
    # For "every": interval in ms
    every_ms: Optional[int] = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: Optional[str] = None
    # Timezone for cron expressions
    tz: Optional[str] = None


@dataclass
class CronPayload:
    """What to do when the job runs."""
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    # Deliver response to channel
    deliver: bool = False
    channel: Optional[str] = None  # e.g. "whatsapp"
    to: Optional[str] = None  # e.g. phone number
    channel_meta: dict = field(default_factory=dict)  # channel-specific routing (e.g. Slack thread_ts)
    session_key: Optional[str] = None  # original session key for correct session recording
    # Dispatch policy for agent messages:
    # - "queue": add to session's pending queue (default)
    # - "idle": only send when session is idle, skip if busy
    # - "interrupt": cancel current task and send immediately
    policy: Literal["queue", "idle", "interrupt"] = "queue"


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""
    run_at_ms: int
    status: Literal["ok", "error", "skipped"]
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: Optional[int] = None
    last_run_at_ms: Optional[int] = None
    last_status: Optional[Literal["ok", "error", "skipped"]] = None
    last_error: Optional[str] = None
    run_history: list[CronRunRecord] = field(default_factory=list)


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False

    @classmethod
    def from_dict(cls, kwargs: dict):
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
