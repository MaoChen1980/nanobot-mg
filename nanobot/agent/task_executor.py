"""TaskExecutor - Goal execution coordinator.

Coordinates goal execution, manages subtasks, calls AgentRunner.
Follows the architecture in task-execution-system.md.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.db import NanobotDB
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.verify.result import VerifierAgent, VerifierResult
from nanobot.providers.base import LLMProvider


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
        self._verifier = VerifierAgent(
            provider=provider,
            tools=tools,
            model=model,
        )

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Hot-swap the provider — recreates runners so new provider state (sessions, auth) is used."""
        self.provider = provider
        self._model = model
        self._runner = AgentRunner(provider, db=self._db)
        self._verifier.set_provider(provider, model)

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

        # === Phase 1b: Dependency check ===
        dep_blocker = self._check_dependencies_blocked(goal_id)
        if dep_blocker:
            self._update_goal_status(goal_id, "blocked")
            return GoalExecutionResult(status="blocked", message=dep_blocker)

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
                goal=goal,
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
                # Also update local copy so loop can progress without DB
                current["status"] = "done"

            # Save checkpoint
            self._save_checkpoint(goal_id, current["id"], result)

            # === Result verification ===
            # Only run if subtask was completed and success_criteria exists
            if self._check_subtask_done(result, current):
                vr = await self._verify_subtask_result(goal, current, result)
                if vr and not vr.passed:
                    logger.warning(
                        "Result verification failed for subtask {}: {}",
                        current["id"], vr.details,
                    )

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
            refreshed = self._get_goal(goal_id)
            if refreshed:
                goal = refreshed

        # === Phase 4: Complete with closure hooks ===
        self._update_goal_status(goal_id, "completed")
        await self._generate_goal_summary(goal_id, goal)
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

    def _build_subtask_messages(
        self,
        goal: dict[str, Any],
        subtask: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build initial messages with goal/subtask context for the LLM."""
        goal_title = goal.get("title", "Untitled")
        goal_desc = goal.get("description", "")
        project = goal.get("project", "")

        subtask_id = subtask.get("id", "?")
        subtask_title = subtask.get("title", subtask_id)

        subtasks = goal.get("data", {}).get("subtasks", [])
        done_count = sum(1 for s in subtasks if s.get("status") == "done")
        total_count = len(subtasks)

        scope = goal.get("scope", {})
        constraints = scope.get("structural_constraints", {})

        parts = [
            f"You are executing a goal. Current context:\n",
            f"## Goal: {goal_title}",
        ]
        if goal_desc:
            parts.append(f"Description: {goal_desc}")
        if project:
            parts.append(f"Project: {project}")

        parts.append("")
        parts.append(f"## Current Subtask: {subtask_title} ({subtask_id})")
        parts.append(f"Progress: {done_count}/{total_count} subtasks completed")
        parts.append("")

        if constraints.get("influential_files"):
            parts.append(f"influential files: {', '.join(constraints['influential_files'])}")
        if constraints.get("file_patterns"):
            parts.append(f"Allowed file patterns: {', '.join(constraints['file_patterns'])}")
        if constraints.get("deny_patterns"):
            parts.append(f"Denied file patterns: {', '.join(constraints['deny_patterns'])}")
        if constraints.get("operation_constraints"):
            parts.append(f"Operation constraints: {', '.join(constraints['operation_constraints'])}")
        if constraints.get("success_criteria"):
            parts.append(f"Success criteria: {', '.join(constraints['success_criteria'])}")

        parts.append("")
        parts.append(f"Use declare_checkpoint when subtask '{subtask_id}' is complete.")

        system_msg = "\n".join(parts)

        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Execute subtask {subtask_id}: {subtask_title}"},
        ]

    async def _execute_subtask(
        self,
        goal_id: str,
        goal: dict[str, Any],
        subtask: dict[str, Any],
        goal_scope: dict[str, Any],
        session_key: str | None,
        context_window_tokens: int | None,
        context_block_limit: int | None,
        provider_retry_mode: str,
    ) -> SubtaskExecutionResult:
        """Execute a single subtask via AgentRunner.

        Builds context messages with goal/subtask info so the LLM
        knows what it's working on. Passes goal_scope to AgentRunSpec
        for StructuralConstraintVerifier checks before tool execution.
        """
        spec = AgentRunSpec(
            initial_messages=self._build_subtask_messages(goal, subtask),
            tools=self._tools,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=self._max_tool_result_chars,
            workspace=self._workspace,
            session_key=session_key,
            context_window_tokens=context_window_tokens,
            context_block_limit=context_block_limit,
            provider_retry_mode=provider_retry_mode,
            goal_scope=goal_scope,
            goal_id=goal_id,
            hook=None,
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
        """Check if subtask is complete based on execution result.

        A subtask is considered done if:
        1. The LLM explicitly called declare_checkpoint for this subtask, OR
        2. The run completed naturally (stop_reason == 'completed')
        """
        # Check if LLM explicitly declared checkpoint for this subtask
        subtask_id = subtask.get("id")
        if self._has_declared_checkpoint(result.messages, subtask_id):
            return True

        # Fallback: normal completion
        return result.stop_reason == "completed"

    def _has_declared_checkpoint(self, messages: list[dict[str, Any]], subtask_id: str) -> bool:
        """Check if any assistant message contains a declare_checkpoint call for subtask_id."""
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            # Check OpenAI format: tool_calls array
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                if fn.get("name") == "declare_checkpoint":
                    try:
                        args = json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else fn.get("arguments", {})
                        if args.get("subtask_id") == subtask_id:
                            return True
                    except (json.JSONDecodeError, TypeError):
                        continue

            # Check Anthropic format: content blocks with type="tool_use"
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("name") == "declare_checkpoint":
                            inp = block.get("input", {})
                            if isinstance(inp, dict) and inp.get("subtask_id") == subtask_id:
                                return True
        return False

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
            content=json.dumps(checkpoint_data, ensure_ascii=False),
            goal_id=goal_id,
        )

    async def _verify_subtask_result(
        self,
        goal: dict[str, Any],
        subtask: dict[str, Any],
        result: SubtaskExecutionResult,
    ) -> VerifierResult | None:
        """Run result verification for a completed subtask.

        Only runs when the goal has success_criteria defined.
        Persists the result as an event in the DB.
        Returns None if no criteria or verifier not available.
        """
        scope = goal.get("scope", {})
        constraints = scope.get("structural_constraints", {})
        if not constraints.get("success_criteria"):
            return None
        vr = await self._verifier.verify(
            goal=goal,
            subtask=subtask,
            final_content=result.final_content,
            tools_used=result.tools_used,
        )

        # Persist verification result as event
        if self._db is not None and vr is not None:
            import json
            goal_id = goal.get("id", "")
            self._db.insert_event(
                event_type="verification",
                content=json.dumps({
                    "subtask_id": subtask.get("id"),
                    "subtask_title": subtask.get("title"),
                    "passed": vr.passed,
                    "details": vr.details,
                    "success_criteria": constraints.get("success_criteria"),
                }, ensure_ascii=False),
                goal_id=goal_id,
                tags=["verification", "passed" if vr.passed else "failed"],
            )

        return vr

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

    def _check_dependencies_blocked(self, goal_id: str) -> str | None:
        """Check if any dependencies are not yet completed.

        Returns None if all dependencies are met, or a blocker message if blocked.
        """
        if self._db is None:
            return None
        deps = self._db.list_dependencies(goal_id)
        if not deps:
            return None
        for dep in deps:
            depended = self._db.get_goal(dep["depends_on"])
            if depended and depended.get("status") != "completed":
                return (
                    f"⚠️ 依赖未完成：goal '{depended['title']}' ({dep['depends_on']}) "
                    f"状态为 '{depended.get('status')}'，需先完成该依赖"
                )
        return None

    async def _generate_goal_summary(self, goal_id: str, goal: dict[str, Any]) -> None:
        """Generate a completion summary event for a completed goal."""
        if self._db is None:
            return
        title = goal.get("title", "")
        subtasks = goal.get("data", {}).get("subtasks", [])
        done_count = sum(1 for s in subtasks if s.get("status") == "done")
        total_count = len(subtasks)

        summary = (
            f"Goal completed: {title}\n"
            f"Subtasks: {done_count}/{total_count} done\n"
            f"Status: completed"
        )
        self._db.insert_event(
            event_type="goal_completed",
            content=summary,
            goal_id=goal_id,
        )
        logger.info("Goal {} ({}) completed. {}/{} subtasks done.", goal_id, title, done_count, total_count)

        # Extract lessons if there were subtasks
        if total_count > 0:
            await self._extract_lessons(goal_id, goal)

    async def _extract_lessons(self, goal_id: str, goal: dict[str, Any]) -> None:
        """Extract lessons from a completed goal via compact LLM call."""
        from nanobot.utils.prompt_templates import render_template

        if self._db is None:
            return

        subtasks = goal.get("data", {}).get("subtasks", [])
        subtask_summary = ", ".join(
            f"{s.get('id')}: {s.get('title')} ({s.get('status')})" for s in subtasks
        )

        events = self._db.list_events(goal_id=goal_id, limit=20)
        verification_lines = []
        for ev in events:
            if ev.get("event_type") == "verification":
                tags = ev.get("tags", [])
                content = ev.get("content", "")[:200]
                verification_lines.append(f"[{tags}]: {content}")
        verification_summary = "\n".join(verification_lines) or "(none)"

        try:
            prompt = render_template(
                "agent/extract_lessons.md",
                title=goal.get("title", ""),
                description=goal.get("description", ""),
                subtask_summary=subtask_summary,
                verification_summary=verification_summary,
                events=events[:15],
                compact_mode=True,
            )
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Extract lessons from the completed goal '{goal.get('title')}'."},
                ],
                model=self._model,
                max_tokens=1024,
                temperature=0.3,
            )
            if response and response.content:
                self._save_lessons(goal_id, goal, response.content)
        except Exception as e:
            logger.warning("Failed to extract lessons for goal {}: {}", goal_id, e)

    def _save_lessons(self, goal_id: str, goal: dict[str, Any], lessons_text: str) -> None:
        """Parse and persist extracted lessons to DB and tasks/lessons.md."""
        if self._db is None:
            return

        lessons = self._parse_lessons_yaml(lessons_text)
        if not lessons:
            return

        for lesson in lessons:
            self._db.insert_lesson(
                goal_id=goal_id,
                lesson_type=lesson.get("type", "optimization"),
                summary=lesson.get("summary", ""),
                detail=lesson.get("detail", ""),
                tags=lesson.get("tags", []),
            )

        # Append to tasks/lessons.md
        if self._workspace:
            lessons_path = self._workspace / "tasks" / "lessons.md"
            try:
                lessons_path.parent.mkdir(parents=True, exist_ok=True)
                with lessons_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n## {goal.get('title', goal_id)} ({goal.get('status', 'completed')})\n\n")
                    for l in lessons:
                        f.write(f"- **{l.get('type')}**: {l.get('summary')}\n")
                        if l.get("detail"):
                            f.write(f"  - {l.get('detail')}\n")
                        if l.get("tags"):
                            f.write(f"  - tags: {l.get('tags')}\n")
                        f.write("\n")
                logger.info("Lessons appended to {} for goal {}", lessons_path, goal_id)
            except Exception as e:
                logger.warning("Failed to append lessons to {}: {}", lessons_path, e)

    @staticmethod
    def _parse_lessons_yaml(text: str) -> list[dict[str, Any]]:
        """Parse structured lessons from LLM output.

        Handles multiple formats:
        - YAML code blocks (```yaml ... ```)
        - Inline YAML list items (- type: ...)
        - Plain text fallback (creates one generic lesson)
        """
        import re
        lessons: list[dict[str, Any]] = []

        # Step 1: extract yaml code blocks
        blocks = re.findall(r'```(?:yaml)?\s*\n?(.*?)```', text, re.DOTALL)
        if not blocks:
            blocks = [text]

        for block in blocks:
            current: dict[str, Any] = {}
            for line in block.strip().split("\n"):
                line_stripped = line.strip()
                # Detect new lesson entry: "- type:" or "type:" at start
                m = re.match(r'^-?\s*type\s*:\s*(.+)$', line_stripped)
                if m:
                    if current and current.get("type"):
                        lessons.append(current)
                    current = {"type": m.group(1).strip().strip('"\'')}
                    continue
                # key: value pairs (indented or not)
                for key in ("summary", "detail", "tags"):
                    m = re.match(rf'^-?\s*{key}\s*:\s*(.+)$', line_stripped)
                    if m:
                        val = m.group(1).strip()
                        if key == "tags":
                            val_clean = re.sub(r'^\[|\]$', '', val)
                            current[key] = [t.strip().strip('"\'') for t in val_clean.split(",") if t.strip()]
                        else:
                            current[key] = val
            if current and current.get("type"):
                lessons.append(current)

        # Step 2: if no structured lessons found, create fallback
        if not lessons:
            lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
            summary = next((l for l in lines if len(l) > 10 and not l.startswith("```")), text[:120])
            lessons.append({
                "type": "optimization",
                "summary": summary[:200],
                "detail": text[:500],
                "tags": [],
            })

        return lessons

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
        for event in reversed(events):
            try:
                content = event.get("content", "")
                if content.startswith("{"):
                    return json.loads(content)
            except (json.JSONDecodeError, Exception):
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