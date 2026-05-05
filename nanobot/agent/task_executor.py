"""TaskExecutor - Goal execution coordinator.

Coordinates goal execution, manages subtasks, calls AgentRunner.
Follows the architecture in task-execution-system.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.db import NanobotDB
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry


MAX_HYPOTHESIS_VERIFICATION_ATTEMPTS = 3


@dataclass
class GoalExecutionResult:
    """Result of a goal execution attempt."""
    status: str  # completed, blocked, paused, in_progress, already_completed
    message: str | None = None
    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    had_injections: bool = False


@dataclass
class SubtaskExecutionResult:
    """Result from a single subtask execution."""
    stop_reason: str  # completed, max_iterations, context_full, interrupted, failed
    final_content: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


class TaskExecutor:
    """Coordinates goal execution with subtask management.

    Responsible for:
    - subtask_0 enforcement (hypothesis verification)
    - checkpoint saving
    - goal completion判定
    - calling AgentRunner for subtask execution
    """

    def __init__(
        self,
        provider: LLMProvider,
        db: NanobotDB | None,
        tools: ToolRegistry,
        model: str,
        max_iterations: int = 50,
        max_tool_result_chars: int = 8000,
        workspace: Path | None = None,
    ):
        self.provider = provider
        self._db = db
        self._tools = tools
        self._model = model
        self._max_iterations = max_iterations
        self._max_tool_result_chars = max_tool_result_chars
        self._workspace = workspace
        self._runner = AgentRunner(provider, db=db)

    async def execute_goal(
        self,
        goal_id: str,
        goal: dict[str, Any],
        session_key: str | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        provider_retry_mode: str = "standard",
    ) -> GoalExecutionResult:
        """Execute a goal to completion or blocker.

        Args:
            goal_id: The goal ID
            goal: Goal data from DB (must have id, title, status, scope, data)
            session_key: Session key for logging
            other params: Passed to AgentRunSpec

        Returns:
            GoalExecutionResult with status and details
        """
        goal_status = goal.get("status", "in_progress")
        goal_scope = goal.get("scope", {})
        goal_data = goal.get("data", {})

        # === Phase 1: Initialization checks ===
        if goal_status == "completed":
            return GoalExecutionResult(status="already_completed")

        if goal_status == "blocked":
            # Check if can resume
            hyp = goal_data.get("hypothesis_verification", {})
            if hyp.get("verdict") == "failed" and not self._can_resume_after_block(hyp):
                return GoalExecutionResult(
                    status="blocked",
                    message="Hypothesis verification failed. Use /resume_goal to re-verify after adjusting approach.",
                )

        # === Phase 2: subtask_0 enforcement ===
        blocker = self._enforce_subtask_0(goal_id, goal)
        if blocker:
            self._update_goal_status(goal_id, "blocked")
            return GoalExecutionResult(status="blocked", message=blocker)

        # === Phase 3: Mark in_progress and execute subtasks ===
        self._update_goal_status(goal_id, "in_progress")

        while not self._is_goal_complete(goal):
            current = self._get_next_subtask(goal)
            if current is None:
                break

            # Execute subtask via AgentRunner
            result = await self._execute_subtask(
                goal_id=goal_id,
                subtask=current,
                goal_scope=goal_scope,
                session_key=session_key,
                context_window_tokens=context_window_tokens,
                context_block_limit=context_block_limit,
                provider_retry_mode=provider_retry_mode,
            )

            # Check if subtask done
            if self._check_subtask_done(result, current):
                self._mark_subtask_done(goal_id, current["id"])

            # Save checkpoint
            self._save_checkpoint(goal_id, current["id"], result)

            # Check stop conditions
            if result.stop_reason == "context_full":
                return self._build_result(goal_id, status="paused", message="Context window full", result=result)

            if result.stop_reason == "interrupted":
                return self._build_result(goal_id, status="paused", message="User interrupted", result=result)

            if result.stop_reason == "max_iterations":
                # Iteration quota exhausted, checkpoint and continue later
                self._update_goal_status(goal_id, "in_progress")
                return self._build_result(goal_id, status="in_progress", message="Max iterations reached", result=result)

            # Check hypothesis verdict
            hyp = self._get_latest_hypothesis_verification(goal_id)
            if hyp and hyp.get("verdict") == "failed":
                self._update_goal_status(goal_id, "blocked")
                return GoalExecutionResult(
                    status="blocked",
                    message="Hypothesis verification failed. Adjust approach and use /resume_goal to re-verify.",
                    final_content=result.final_content,
                    messages=result.messages,
                    tools_used=result.tools_used,
                    stop_reason=result.stop_reason,
                )

            # Refresh goal data for next iteration
            goal = self._get_goal(goal_id)

        # === Phase 4: Complete ===
        self._update_goal_status(goal_id, "completed")
        return self._build_result(goal_id, status="completed", goal=goal)

    def _enforce_subtask_0(self, goal_id: str, goal: dict[str, Any]) -> str | None:
        """Check if subtask_0 is complete.

        Returns None if passed, blocker message if blocked.
        """
        data = goal.get("data") or {}
        hyp = data.get("hypothesis_verification", {})
        scope = goal.get("scope", {}).get("structural_constraints", {})

        # 1. Check influential_files read
        influential = scope.get("influential_files", [])
        if influential:
            files_read = hyp.get("files_read", [])
            missing = set(influential) - set(files_read)
            if missing:
                return f"⚠️ subtask_0 未完成：未读取 {missing}"

        # 2. Check assumption declared
        if not hyp.get("assumption"):
            return "⚠️ subtask_0 未完成：未声明假设（调用 declare_assumption）"

        # 3. Check verification executed
        attempts = hyp.get("verification_attempts", [])
        if not attempts:
            return "⚠️ subtask_0 未完成：未执行验证"

        # 4. Check verdict exists
        if not hyp.get("verdict"):
            return "⚠️ subtask_0 未完成：未获得验证结论"

        return None

    async def _execute_subtask(
        self,
        goal_id: str,
        subtask: dict[str, Any],
        goal_scope: dict[str, Any],
        session_key: str | None,
        context_window_tokens: int | None,
        context_block_limit: int | None,
        provider_retry_mode: str,
    ) -> SubtaskExecutionResult:
        """Execute a single subtask via AgentRunner.

        Passes goal_scope to AgentRunSpec so AgentRunner can do
        StructuralConstraintVerifier checks before tool execution.
        """
        spec = AgentRunSpec(
            initial_messages=[],
            tools=self._tools,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=self._max_tool_result_chars,
            workspace=self._workspace,
            session_key=session_key,
            context_window_tokens=context_window_tokens,
            context_block_limit=context_block_limit,
            provider_retry_mode=provider_retry_mode,
            # Pass goal_scope for constraint verification
            hook=None,  # Could pass a custom hook for goal tracking
        )

        result = await self._runner.run(spec)

        return SubtaskExecutionResult(
            stop_reason=result.stop_reason,
            final_content=result.final_content,
            messages=result.messages,
            tools_used=result.tools_used,
        )

    def _is_goal_complete(self, goal: dict[str, Any]) -> bool:
        """Check if all subtasks are done and hypothesis passed."""
        subtasks = goal.get("data", {}).get("subtasks", [])

        # 1. All subtasks done
        if not all(s.get("status") == "done" for s in subtasks):
            return False

        # 2. s0 hypothesis passed (not failed)
        hyp = goal.get("data", {}).get("hypothesis_verification", {})
        if hyp.get("verdict") == "failed":
            return False

        return True

    def _check_subtask_done(self, result: SubtaskExecutionResult, subtask: dict[str, Any]) -> bool:
        """Check if subtask is complete based on execution result."""
        # For now, assume subtask completes when iteration finishes
        # A more sophisticated version would check checkpoint_callback or explicit declaration
        return result.stop_reason == "completed"

    def _get_next_subtask(self, goal: dict[str, Any]) -> dict[str, Any] | None:
        """Get the next uncompleted subtask."""
        subtasks = goal.get("data", {}).get("subtasks", [])
        for s in subtasks:
            if s.get("status") != "done":
                return s
        return None

    def _mark_subtask_done(self, goal_id: str, subtask_id: str) -> None:
        """Mark a subtask as done in DB."""
        if self._db is None:
            return
        goal = self._db.get_goal(goal_id)
        if not goal:
            return
        data = goal.get("data", {})
        subtasks = data.get("subtasks", [])
        for s in subtasks:
            if s.get("id") == subtask_id:
                s["status"] = "done"
                break
        self._db.upsert_goal(
            id=goal_id,
            title=goal.get("title", ""),
            status=goal.get("status", "in_progress"),
            data=data,
        )

    def _save_checkpoint(
        self,
        goal_id: str,
        subtask_id: str,
        result: SubtaskExecutionResult,
    ) -> None:
        """Save checkpoint for recovery."""
        if self._db is None:
            return
        # Store checkpoint in events table
        checkpoint_data = {
            "subtask_id": subtask_id,
            "stop_reason": result.stop_reason,
            "final_content": result.final_content,
            "tools_used": result.tools_used,
        }
        self._db.insert_event(
            event_type="checkpoint",
            content=str(checkpoint_data),
            goal_id=goal_id,
        )

    def _get_latest_hypothesis_verification(self, goal_id: str) -> dict[str, Any] | None:
        """Get latest hypothesis verification from goal data."""
        if self._db is None:
            return None
        goal = self._db.get_goal(goal_id)
        if not goal:
            return None
        return goal.get("data", {}).get("hypothesis_verification")

    def _can_resume_after_block(self, hyp: dict[str, Any]) -> bool:
        """Check if hypothesis can be re-verified (not exhausted attempts)."""
        attempts = hyp.get("verification_attempts", [])
        return len(attempts) < MAX_HYPOTHESIS_VERIFICATION_ATTEMPTS

    def _get_goal(self, goal_id: str) -> dict[str, Any] | None:
        """Get fresh goal data from DB."""
        if self._db is None:
            return None
        return self._db.get_goal(goal_id)

    def _update_goal_status(self, goal_id: str, status: str) -> None:
        """Update goal status in DB."""
        if self._db is None:
            return
        goal = self._db.get_goal(goal_id)
        if goal:
            self._db.upsert_goal(
                id=goal_id,
                title=goal.get("title", ""),
                status=status,
                data=goal.get("data", {}),
            )

    def _build_result(
        self,
        goal_id: str,
        status: str,
        message: str | None = None,
        result: SubtaskExecutionResult | None = None,
        goal: dict[str, Any] | None = None,
    ) -> GoalExecutionResult:
        """Build a GoalExecutionResult from components."""
        if result:
            return GoalExecutionResult(
                status=status,
                message=message,
                final_content=result.final_content,
                tools_used=result.tools_used,
                messages=result.messages,
                stop_reason=result.stop_reason,
            )
        return GoalExecutionResult(status=status, message=message)

    async def resume_goal(
        self,
        goal_id: str,
        session_key: str | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        provider_retry_mode: str = "standard",
    ) -> GoalExecutionResult:
        """Resume a blocked or paused goal.

        Args:
            goal_id: Goal to resume
            session_key: Session key
            other params: Passed through to execute_goal

        Returns:
            GoalExecutionResult
        """
        goal = self._get_goal(goal_id)
        if not goal:
            return GoalExecutionResult(status="error", message=f"Goal {goal_id} not found")

        current_status = goal.get("status", "in_progress")

        # Resume blocked goal - re-check subtask_0
        if current_status == "blocked":
            # Re-run subtask_0 enforcement
            blocker = self._enforce_subtask_0(goal_id, goal)
            if blocker:
                return GoalExecutionResult(status="blocked", message=blocker)
            self._update_goal_status(goal_id, "in_progress")

        # Resume paused goal - find latest checkpoint and continue from there
        if current_status == "paused":
            checkpoint = self._get_latest_checkpoint(goal_id)
            if checkpoint:
                # Resume from checkpoint - find the next incomplete subtask
                next_subtask = self._get_next_subtask_after_checkpoint(goal, checkpoint)
                if next_subtask:
                    # Re-execute from the next subtask
                    goal_data = goal.get("data", {})
                    goal_data["_checkpoint_resume"] = checkpoint
                    goal["data"] = goal_data

        return await self.execute_goal(
            goal_id=goal_id,
            goal=goal,
            session_key=session_key,
            context_window_tokens=context_window_tokens,
            context_block_limit=context_block_limit,
            provider_retry_mode=provider_retry_mode,
        )

    def _get_latest_checkpoint(self, goal_id: str) -> dict[str, Any] | None:
        """Get the latest checkpoint for a goal."""
        if self._db is None:
            return None
        events = self._db.list_events(goal_id=goal_id, event_type="checkpoint", limit=10)
        if not events:
            return None
        # Parse checkpoint from the most recent event
        import json
        for event in reversed(events):
            try:
                content = event.get("content", "")
                if content.startswith("{"):
                    return json.loads(content)
                # Try to parse as Python dict representation
                checkpoint_data = eval(content)  # Safe here since we control the format
                return checkpoint_data
            except Exception:
                continue
        return None

    def _get_next_subtask_after_checkpoint(
        self,
        goal: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Get the next subtask after a checkpoint."""
        subtasks = goal.get("data", {}).get("subtasks", [])
        completed_ids = set()
        # Collect all checkpoints to find what was completed
        # A checkpoint with stop_reason='completed' means the subtask finished
        if checkpoint.get("stop_reason") == "completed":
            completed_ids.add(checkpoint.get("subtask_id"))
        # Find first non-completed subtask
        for s in subtasks:
            if s.get("id") not in completed_ids and s.get("status") != "done":
                return s
        return None if subtasks else None