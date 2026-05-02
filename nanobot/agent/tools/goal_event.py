"""Goal and event management tools for structured context.

LLM calls these to write structured data to the DB instead of writing Markdown files.
Read paths (context assembly) go directly through memory._db - these tools are for writes only.
"""

from datetime import datetime, timezone
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Schema, Tool
from nanobot.agent.tools.schema import (
    ArraySchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
)


class WriteGoalSchema(Schema):
    params = ObjectSchema(
        properties={
            "id": StringSchema(description="Goal ID, e.g. 'g10'. Use 'g{N}' pattern."),
            "title": StringSchema(description="Short goal title"),
            "action": StringSchema(
                description="'upsert' to create or update, 'delete' to remove",
                enum=["upsert", "delete"],
            ),
            "status": StringSchema(
                description="Goal status (only for upsert)",
                enum=["in_progress", "completed", "paused", "archived"],
            ),
            "project": StringSchema(description="Project name this goal belongs to"),
            "description": StringSchema(description="Goal description"),
            "subtasks": ArraySchema(
                items=ObjectSchema(
                    properties={
                        "id": StringSchema(description="Subtask ID"),
                        "title": StringSchema(description="Subtask title"),
                        "status": StringSchema(description="Status: todo/done"),
                    }
                ),
                description="Subtasks list",
            ),
            "notes": ArraySchema(items=StringSchema(""), description="Additional notes"),
            "blockers": ArraySchema(items=StringSchema(""), description="Blocking issues"),
        },
        required=["id", "title", "action"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class WriteGoal(Tool):
    """Create or update a goal in structured DB (not Markdown file).

    Use this instead of editing goals.md. Goals are stored in SQLite and
    queried by context.py during prompt assembly.
    """

    name = "write_goal"
    description = "Create or update a goal. Goals are stored in DB, not files."
    param_schema = WriteGoalSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        id: str,
        title: str,
        action: str,
        status: str | None = None,
        project: str | None = None,
        description: str = "",
        subtasks: list[dict[str, str]] | None = None,
        notes: list[str] | None = None,
        blockers: list[str] | None = None,
    ) -> str:
        if action == "delete":
            if self._memory._db is not None:
                self._memory._db.delete_goal(id)
            return f"Goal '{id}' deleted."

        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "subtasks": subtasks or [],
            "notes": notes or [],
            "blockers": blockers or [],
        }
        if self._memory._db is not None:
            self._memory._db.upsert_goal(
                id=id,
                title=title,
                status=status or "in_progress",
                project=project,
                description=description,
                data=data,
                updated_at=ts,
            )
            return f"Goal '{id}' upserted: {title}"
        return f"DB not available, cannot upsert goal '{id}'"


class ListGoalsSchema(Schema):
    params = ObjectSchema(
        properties={
            "status": StringSchema(
                description="Filter by status",
                enum=["in_progress", "completed", "paused", "archived"],
            ),
            "project": StringSchema(description="Filter by project"),
            "limit": NumberSchema(description="Max results (integer)", minimum=1, maximum=100),
        },
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class ListGoals(Tool):
    """List goals from structured DB."""

    name = "list_goals"
    description = "List goals from DB. Filter by status/project."
    param_schema = ListGoalsSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        status: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"
        goals = self._memory._db.list_goals(status=status, project=project)
        if not goals:
            return "No goals found."
        lines = []
        for g in goals[:limit]:
            lines.append(f"[{g['status']}] {g['id']}: {g['title']}")
            if g.get("description"):
                lines.append(f"  {g['description']}")
        return "\n".join(lines)


class WriteEventSchema(Schema):
    params = ObjectSchema(
        properties={
            "content": StringSchema(description="Event description"),
            "action": StringSchema(
                description="Event type",
                enum=["log", "milestone", "decision", "blocker"],
            ),
            "goal_id": StringSchema(description="Associated goal ID"),
            "tags": ArraySchema(items=StringSchema(""), description="Tags for filtering"),
            "timestamp": StringSchema(description="ISO timestamp (auto-generated if not provided)"),
        },
        required=["content", "action"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class WriteEvent(Tool):
    """Log a progress event to structured DB (not process-log.md).

    Use this to record milestones, decisions, blockers, and progress updates.
    """

    name = "write_event"
    description = "Log an event (progress/milestone/decision/blocker) to DB."
    param_schema = WriteEventSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        content: str,
        action: str,
        goal_id: str | None = None,
        tags: list[str] | None = None,
        timestamp: str | None = None,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"
        event_type_map = {
            "log": "progress",
            "milestone": "milestone",
            "decision": "decision",
            "blocker": "blocker",
        }
        event_type = event_type_map.get(action, action)
        event_id = self._memory._db.insert_event(
            event_type=event_type,
            content=content,
            goal_id=goal_id,
            tags=tags or [],
            timestamp=timestamp,
        )
        return f"Event logged (id={event_id}): [{action}] {content}"


class ListEventsSchema(Schema):
    params = ObjectSchema(
        properties={
            "goal_id": StringSchema(description="Filter by goal"),
            "event_type": StringSchema(
                description="Event type",
                enum=["progress", "milestone", "decision", "blocker"],
            ),
            "limit": NumberSchema(description="Max results (integer)", minimum=1, maximum=100),
        },
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class ListEvents(Tool):
    """List recent events from DB."""

    name = "list_events"
    description = "List recent events from DB. Filter by goal/type."
    param_schema = ListEventsSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        goal_id: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"
        events = self._memory._db.list_events(
            goal_id=goal_id,
            event_type=event_type,
            limit=limit,
        )
        if not events:
            return "No events found."
        lines = []
        for e in events:
            ts = e["timestamp"][:26] if e["timestamp"] else "?"
            lines.append(f"[{ts}] [{e['event_type']}] {e['content']}")
        return "\n".join(lines)


def register(memory: MemoryStore) -> list[Tool]:
    """Register goal/event tools with the tool registry."""
    return [WriteGoal(memory), ListGoals(memory), WriteEvent(memory), ListEvents(memory)]