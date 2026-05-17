"""Goal and event management tools for structured context.

LLM calls these to write structured data to the DB instead of writing Markdown files.
Read paths (context assembly) go directly through memory._db - these tools are for writes only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Schema, Tool, tool_parameters
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "id": p("string", "Goal ID, e.g. 'g10'. Use 'g{N}' pattern."),
    "title": p("string", "Short goal title"),
    "action": p("string",
        "Action: 'upsert' to create/update, 'delete' to remove",
        enum=["upsert", "delete"],
    ),
    "status": p("string",
        "Goal status (only for upsert)",
        enum=["in_progress", "completed", "paused", "archived"],
    ),
    "project": p("string", "Project name this goal belongs to"),
    "bot": p("string", "Bot name this goal belongs to"),
    "description": p("string", "Goal description"),
    "subtasks": p("array", "Subtasks list", items={
        "type": "object",
        "properties": {
            "id": p("string", "Subtask ID"),
            "title": p("string", "Subtask title"),
            "status": p("string", "Status: todo (未开始) / done (已完成)"),
        },
    }),
    "scopes": p("array",
        "Functional scopes this goal belongs to. Uses hierarchical naming: "
        "'module' for broad, 'module/submodule' for specific. "
        "Examples: ['memory'], ['agent/loop'], ['tools/shell']",
        items=p("string", ""),
    ),
    "notes": p("array", "Additional notes", items=p("string", "")),
    "blockers": p("array", "Blocking issues — free text descriptions of what's blocking this goal", items=p("string", "")),
}, required=["id", "title", "action"])
class WriteGoal(Tool):
    """Create or update a goal in structured DB.

    Goals are stored in SQLite and queried by context.py during prompt assembly.
    """

    name = "write_goal"
    description = (
        "**用途**: 创建或更新目标（goal），目标跨会话跟踪。\n\n"
        "**限制**:\n"
        "- 存储在 SQLite DB，非文件\n"
        "- 每次 turn 自动加载到 context\n"
        "- 必须提供 id、title、action\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回错误\n"
        "- goal_id 重复 → upsert 语义，覆盖更新\n\n"
        "**边界条件**:\n"
        "- 只需临时笔记 → 用 scratchpad（self set）\n"
        "- 只需查看目标 → 用 list_goals\n"
        "- project 未指定且含 '.' → 自动继承父 goal 的 project\n\n"
        "**极简案例**: write_goal(id='g1', title='实现登录功能', action='upsert', status='in_progress')\n"
        "→ 创建新目标"
    )

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
        bot: str | None = None,
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

        # Auto-inherit project from parent goal if not specified
        if project is None and self._memory._db is not None:
            parent_id = self._get_parent_goal_id(id)
            if parent_id:
                parent = self._memory._db.get_goal(parent_id)
                if parent and parent.get("project"):
                    project = parent["project"]

        if self._memory._db is not None:
            self._memory._db.upsert_goal(
                id=id,
                title=title,
                status=status or "in_progress",
                project=project,
                bot=bot,
                description=description,
                data=data,
                updated_at=ts,
            )
            inherited_msg = f" (inherited project={project})" if project else ""
            return f"Goal '{id}' upserted: {title}{inherited_msg}"
        return f"DB not available, cannot upsert goal '{id}'"

    def _get_parent_goal_id(self, goal_id: str) -> str | None:
        """Get parent goal ID by stripping last segment after '.'."""
        if '.' in goal_id:
            parts = goal_id.rsplit('.', 1)
            return parts[0]
        return None


@tool_parameters(properties={
    "status": p("string",
        "Filter by status",
        enum=["in_progress", "completed", "paused", "archived"],
    ),
    "project": p("string", "Filter by project"),
    "scope": p("string", "Filter by scope (e.g. 'memory', 'agent/loop')"),
    "bot": p("string", "Filter by bot name"),
    "limit": p("number", "Max results (integer, default 20)", minimum=1, maximum=100, default=20),
})
class ListGoals(Tool):
    """List goals from structured DB."""

    name = "list_goals"
    description = (
        "**用途**: 从 DB 列出目标，可按状态/项目/范围过滤。\n\n"
        "**限制**:\n"
        "- 最多返回 100 条\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回 'DB not available'\n"
        "- 无匹配 → 返回 'No goals found'\n\n"
        "**边界条件**:\n"
        "- 需要更新目标 → 用 write_goal\n"
        "- 需要查看事件历史 → 用 list_events\n\n"
        "**极简案例**: list_goals(status='in_progress')\n"
        "→ 列出所有进行中的目标"
    )

    def __init__(self, memory: MemoryStore):
        super().__init__()
        self._memory = memory

    async def execute(
        self,
        status: str | None = None,
        project: str | None = None,
        scope: str | None = None,
        bot: str | None = None,
        limit: int = 20,
    ) -> str:
        if self._memory._db is None:
            return "DB not available"
        goals = self._memory._db.list_goals(status=status, project=project, scope=scope, bot=bot)
        if not goals:
            return "No goals found."
        lines = []
        for g in goals[:limit]:
            lines.append(f"[{g['status']}] {g['id']}: {g['title']}")
            if g.get("description"):
                lines.append(f"  {g['description']}")
        return "\n".join(lines)


_WRITE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": p("string", "Event description"),
        "action": p("string", "Event type", enum=["log", "milestone", "decision", "blocker"]),
        "goal_id": p("string", "Associated goal ID"),
        "tags": p("array", "Tags for filtering", items=p("string", "")),
        "timestamp": p("string", "ISO timestamp (auto-generated if not provided)"),
    },
    "required": ["content", "action"],
}


def _validate_write_event(val: Any) -> list[str]:
    return Schema.validate_json_schema_value(val, _WRITE_EVENT_SCHEMA, "")


@tool_parameters(schema=_WRITE_EVENT_SCHEMA)
class WriteEvent(Tool):
    """Log a progress event to structured DB.

    Use this to record milestones, decisions, blockers, and progress updates.
    """

    name = "write_event"
    description = (
        "**用途**: 记录进度事件（里程碑、决策、阻塞项）到当前目标的时间线。\n\n"
        "**限制**:\n"
        "- 必须提供 content 和 action\n"
        "- 存储在 DB，关联 goal_id\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回 'DB not available'\n\n"
        "**边界条件**:\n"
        "- 信息属于 goal 描述本身 → 用 write_goal 更新描述\n"
        "- 不关联任何目标 → 不要用\n\n"
        "**极简案例**: write_event(content='完成 API 设计评审', action='milestone', goal_id='g1')\n"
        "→ 记录里程碑事件"
    )

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


_LIST_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal_id": p("string", "Filter by goal"),
        "event_type": p("string",
            "Event type",
            enum=["progress", "milestone", "decision", "blocker"],
        ),
        "limit": p("number", "Max results (integer, default 10)", minimum=1, maximum=100, default=10),
    },
}


def _validate_list_events(val: Any) -> list[str]:
    return Schema.validate_json_schema_value(val, _LIST_EVENTS_SCHEMA, "")


@tool_parameters(schema=_LIST_EVENTS_SCHEMA)
class ListEvents(Tool):
    """List recent events from DB."""

    name = "list_events"
    description = (
        "**用途**: 列出最近的事件，可按目标或事件类型过滤。\n\n"
        "**限制**:\n"
        "- 最多返回 100 条\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回 'DB not available'\n"
        "- 无匹配 → 返回 'No events found'\n\n"
        "**边界条件**:\n"
        "- 需要查看活跃目标 → 用 list_goals\n"
        "- 需要记录新事件 → 用 write_event\n\n"
        "**极简案例**: list_events(goal_id='g1', limit=5)\n"
        "→ 查看目标 g1 最近的 5 个事件"
    )

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


_DECLARE_ASSUMPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal_id": p("string", "Goal ID to declare assumption for"),
        "claim": p("string", "The hypothesis/assumption claim (what you expect to be true)"),
        "expected": p("string", "Expected value or state after verification"),
        "files_read": p("array",
            "List of file paths read to inform this assumption",
            items=p("string", ""),
        ),
        "verification_method": p("string", "How to verify: 'read_file', 'grep', 'exec', etc."),
    },
    "required": ["goal_id", "claim", "expected", "files_read", "verification_method"],
}


def _validate_declare_assumption(val: Any) -> list[str]:
    return Schema.validate_json_schema_value(val, _DECLARE_ASSUMPTION_SCHEMA, "")


@tool_parameters(schema=_DECLARE_ASSUMPTION_SCHEMA)
class DeclareAssumption(Tool):
    """Declare a hypothesis assumption for goal subtask_0.

    This must be called before proceeding past subtask_0.
    The verification result is determined by the system (not LLM self-report).
    """

    name = "declare_assumption"
    description = (
        "**用途**: 为目标 subtask_0 声明假设（hypothesis），系统验证。\n\n"
        "**限制**:\n"
        "- 必须在完成 subtask_0 之前调用\n"
        "- 结果由系统判定（非 LLM 自评）\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回 'DB not available'\n"
        "- goal 不存在 → 返回错误\n\n"
        "**边界条件**:\n"
        "- 已有 assumption → 追加为新的 verification attempt\n\n"
        "**极简案例**: declare_assumption(goal_id='g1', claim='接口已就绪', expected='200 OK', files_read=['main.py'], verification_method='exec')\n"
        "→ 声明假设等待验证"
    )

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


_VERIFY_ASSUMPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal_id": p("string", "Goal ID to verify"),
        "actual": p("string", "Actual observed value/state"),
    },
    "required": ["goal_id", "actual"],
}


def _validate_verify_assumption(val: Any) -> list[str]:
    return Schema.validate_json_schema_value(val, _VERIFY_ASSUMPTION_SCHEMA, "")


@tool_parameters(schema=_VERIFY_ASSUMPTION_SCHEMA)
class VerifyAssumption(Tool):
    """Verify a hypothesis assumption by comparing expected vs actual.

    This is called by the system (not LLM) to make the verdict.
    The verdict is passed=True only if actual matches expected.
    """

    name = "verify_assumption"
    description = (
        "**用途**: 验证假设 — 系统比较 expected 和 actual 判定 verdict。\n\n"
        "**限制**:\n"
        "- 由系统调用（非 LLM）\n"
        "- passed=True 仅当 actual == expected\n\n"
        "**错误应对**:\n"
        "- goal 不存在 → 返回错误\n"
        "- 未先 declare_assumption → 返回错误\n\n"
        "**边界条件**:\n"
        "- 必须先调用 declare_assumption\n\n"
        "**极简案例**: verify_assumption(goal_id='g1', actual='200 OK')\n"
        "→ 系统验证假设是否通过"
    )

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


_DECLARE_CHECKPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal_id": p("string", "Goal ID"),
        "subtask_id": p("string", "Subtask ID being completed"),
        "summary": p("string", "Summary of what was accomplished"),
        "artifacts": p("array", "List of artifacts produced", items={
            "type": "object",
            "properties": {
                "type": p("string", "Artifact type"),
                "path": p("string", "File path or reference"),
                "description": p("string", "Description"),
            },
        }),
    },
    "required": ["goal_id", "subtask_id", "summary"],
}


def _validate_declare_checkpoint(val: Any) -> list[str]:
    return Schema.validate_json_schema_value(val, _DECLARE_CHECKPOINT_SCHEMA, "")


@tool_parameters(schema=_DECLARE_CHECKPOINT_SCHEMA)
class DeclareCheckpoint(Tool):
    """Declare a subtask checkpoint - marks subtask as done with summary and artifacts.

    Does NOT enforce that this is the current subtask - allows early completion.
    """

    name = "declare_checkpoint"
    description = (
        "**用途**: 声明子任务已完成，附带摘要和可选产出物。\n\n"
        "**限制**:\n"
        "- 不强制子任务顺序 — 允许提前/跳过完成\n\n"
        "**错误应对**:\n"
        "- DB 不可用 → 返回 'DB not available'\n"
        "- goal 不存在 → 返回错误\n\n"
        "**边界条件**:\n"
        "- 当前子任务与你声明的不一致 → 返回警告但不阻止\n\n"
        "**极简案例**: declare_checkpoint(goal_id='g1', subtask_id='s0', summary='完成需求分析')\n"
        "→ 声明子任务完成"
    )

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
            content=json.dumps(checkpoint_data, ensure_ascii=False),
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
