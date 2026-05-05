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
            "scopes": ArraySchema(
                items=StringSchema(""),
                description="Functional scopes this goal belongs to (e.g. ['memory', 'agent/loop'])",
            ),
            "notes": ArraySchema(items=StringSchema(""), description="Additional notes"),
            "blockers": ArraySchema(items=StringSchema(""), description="Blocking issues"),
        },
        required=["id", "title", "action"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class WriteGoal(Tool):
    """Create or update a goal in structured DB.

    Goals are stored in SQLite and queried by context.py during prompt assembly.
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
        scopes: list[str] | None = None,
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
            "scopes": scopes or [],
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
            "scope": StringSchema(description="Filter by scope (e.g. 'memory', 'agent/loop')"),
            "limit": NumberSchema(description="Max results (integer)", minimum=1, maximum=100),
        },
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class ListGoals(Tool):
    """List goals from structured DB."""

    name = "list_goals"
    description = "List goals from DB. Filter by status/project/scope."
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
        scope: str | None = None,
        limit: int = 20,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"
        goals = self._memory._db.list_goals(status=status, project=project, scope=scope)
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
    """Log a progress event to structured DB.

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


class DeclareAssumptionSchema(Schema):
    params = ObjectSchema(
        properties={
            "goal_id": StringSchema(description="Goal ID to declare assumption for"),
            "claim": StringSchema(description="The hypothesis/assumption claim (what you expect to be true)"),
            "expected": StringSchema(description="Expected value or state after verification"),
            "files_read": ArraySchema(
                items=StringSchema(""),
                description="List of file paths read to inform this assumption",
            ),
            "verification_method": StringSchema(
                description="How to verify: 'read_file', 'grep', 'exec', etc.",
            ),
        },
        required=["goal_id", "claim", "expected", "files_read", "verification_method"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class DeclareAssumption(Tool):
    """Declare a hypothesis assumption for goal subtask_0.

    This must be called before proceeding past subtask_0.
    The verification result is determined by the system (not LLM self-report).
    """

    name = "declare_assumption"
    description = "Declare hypothesis assumption for subtask_0. Must be called before proceeding past subtask_0."
    param_schema = DeclareAssumptionSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        goal_id: str,
        claim: str,
        expected: str,
        files_read: list[str],
        verification_method: str,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"

        goal = self._memory._db.get_goal(goal_id)
        if not goal:
            return f"Goal '{goal_id}' not found"

        data = goal.get("data", {})
        hyp = data.get("hypothesis_verification", {})

        # Build assumption object
        assumption = {
            "claim": claim,
            "expected": expected,
            "files_read": files_read,
            "verification_method": verification_method,
        }

        # Check if already has assumption (append to verification_attempts)
        if hyp.get("assumption"):
            # Already declared - this is a retry
            hyp["verification_attempts"].append({
                "assumption": assumption,
                "result": None,
                "verdict": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            # First declaration
            hyp["assumption"] = assumption
            hyp["verification_attempts"] = [{
                "assumption": assumption,
                "result": None,
                "verdict": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
            hyp["verdict"] = None

        data["hypothesis_verification"] = hyp
        self._memory._db.upsert_goal(
            id=goal_id,
            title=goal.get("title", ""),
            status=goal.get("status", "in_progress"),
            data=data,
        )

        return (
            f"Assumption declared for goal '{goal_id}':\n"
            f"  Claim: {claim}\n"
            f"  Expected: {expected}\n"
            f"  Files read: {files_read}\n"
            f"  Verification method: {verification_method}\n"
            f"Use verify_assumption to complete subtask_0 verification."
        )


class VerifyAssumptionSchema(Schema):
    params = ObjectSchema(
        properties={
            "goal_id": StringSchema(description="Goal ID to verify"),
            "actual": StringSchema(description="Actual observed value/state"),
        },
        required=["goal_id", "actual"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class VerifyAssumption(Tool):
    """Verify a hypothesis assumption by comparing expected vs actual.

    This is called by the system (not LLM) to make the verdict.
    The verdict is passed=True only if actual matches expected.
    """

    name = "verify_assumption"
    description = "Verify hypothesis assumption - system compares expected vs actual to determine verdict."
    param_schema = VerifyAssumptionSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        goal_id: str,
        actual: str,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"

        goal = self._memory._db.get_goal(goal_id)
        if not goal:
            return f"Goal '{goal_id}' not found"

        data = goal.get("data", {})
        hyp = data.get("hypothesis_verification", {})

        assumption = hyp.get("assumption")
        if not assumption:
            return f"No assumption declared for goal '{goal_id}'. Call declare_assumption first."

        expected = assumption.get("expected", "")

        # System makes the verdict - not LLM
        passed = expected == actual

        # Update current attempt
        attempts = hyp.get("verification_attempts", [])
        if attempts:
            attempts[-1]["result"] = {"actual": actual, "expected": expected}
            attempts[-1]["verdict"] = "passed" if passed else "failed"

        hyp["verdict"] = "passed" if passed else "failed"

        # Update subtask status if s0
        subtasks = data.get("subtasks", [])
        if subtasks and subtasks[0].get("id") == "s0":
            subtasks[0]["status"] = "done"

        data["hypothesis_verification"] = hyp
        self._memory._db.upsert_goal(
            id=goal_id,
            title=goal.get("title", ""),
            status=goal.get("status", "in_progress"),
            data=data,
        )

        verdict = "PASSED" if passed else "FAILED"
        return (
            f"Verification result for goal '{goal_id}':\n"
            f"  Expected: {expected}\n"
            f"  Actual: {actual}\n"
            f"  Verdict: {verdict}"
        )


class DeclareCheckpointSchema(Schema):
    params = ObjectSchema(
        properties={
            "goal_id": StringSchema(description="Goal ID"),
            "subtask_id": StringSchema(description="Subtask ID being completed"),
            "summary": StringSchema(description="Summary of what was accomplished"),
            "artifacts": ArraySchema(
                items=ObjectSchema(
                    properties={
                        "type": StringSchema(description="Artifact type"),
                        "path": StringSchema(description="File path or reference"),
                        "description": StringSchema(description="Description"),
                    }
                ),
                description="List of artifacts produced",
            ),
        },
        required=["goal_id", "subtask_id", "summary"],
    )

    def validate_value(self, val: Any) -> list[str]:
        return self.validate_json_schema_value(val, self.params)


class DeclareCheckpoint(Tool):
    """Declare a subtask checkpoint - marks subtask as done with summary and artifacts.

    Does NOT enforce that this is the current subtask - allows early completion.
    """

    name = "declare_checkpoint"
    description = "Declare a subtask checkpoint - marks subtask as done with summary and artifacts."
    param_schema = DeclareCheckpointSchema

    @property
    def parameters(self) -> dict[str, Any]:
        return self.param_schema.params.to_json_schema()

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        goal_id: str,
        subtask_id: str,
        summary: str,
        artifacts: list[dict[str, str]] | None = None,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"

        goal = self._memory._db.get_goal(goal_id)
        if not goal:
            return f"Goal '{goal_id}' not found"

        data = goal.get("data", {})
        subtasks = data.get("subtasks", [])

        # Check current subtask for warning
        current = None
        for s in subtasks:
            if s.get("status") != "done":
                current = s
                break

        warning = None
        if current and current.get("id") != subtask_id:
            warning = f"注意：当前 subtask 是 {current['id']}，你声明的是 {subtask_id}"

        # Mark subtask done
        for s in subtasks:
            if s.get("id") == subtask_id:
                s["status"] = "done"
                break

        # Save checkpoint as event
        checkpoint_data = {
            "subtask_id": subtask_id,
            "summary": summary,
            "artifacts": artifacts or [],
        }
        self._memory._db.insert_event(
            event_type="checkpoint",
            content=str(checkpoint_data),
            goal_id=goal_id,
        )

        self._memory._db.upsert_goal(
            id=goal_id,
            title=goal.get("title", ""),
            status=goal.get("status", "in_progress"),
            data=data,
        )

        msg = f"Checkpoint declared for goal '{goal_id}', subtask '{subtask_id}': {summary}"
        if warning:
            msg += f"\n{warning}"
        return msg


def register(memory: MemoryStore) -> list[Tool]:
    """Register goal/event tools with the tool registry."""
    return [
        WriteGoal(memory),
        ListGoals(memory),
        WriteEvent(memory),
        ListEvents(memory),
        DeclareAssumption(memory),
        VerifyAssumption(memory),
        DeclareCheckpoint(memory),
    ]