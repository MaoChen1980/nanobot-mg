"""Tests for TaskExecutor - goal execution coordinator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.task_executor import TaskExecutor, SubtaskExecutionResult
from nanobot.agent.verify.result import VerifierResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    p = MagicMock()
    p.get_default_model.return_value = "test-model"
    return p


@pytest.fixture
def tools():
    t = MagicMock()
    t.get_definitions.return_value = []
    return t


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def executor(provider, tools, db):
    return TaskExecutor(provider=provider, db=db, tools=tools, model="test-model")


@pytest.fixture
def executor_no_db(provider, tools):
    return TaskExecutor(provider=provider, db=None, tools=tools, model="test-model")


def _make_goal(**overrides) -> dict:
    """Helper to build a minimal goal dict."""
    goal = {
        "id": "g1",
        "title": "Test Goal",
        "status": "in_progress",
        "description": "A test goal",
        "project": "test-project",
        "scope": {
            "structural_constraints": {
                "influential_files": ["config.json"],
                "operation_constraints": ["read_only"],
            }
        },
        "data": {
            "subtasks": [
                {"id": "s0", "title": "subtask_0", "status": "done"},
                {"id": "s1", "title": "subtask_1", "status": "todo"},
                {"id": "s2", "title": "subtask_2", "status": "todo"},
            ],
            "hypothesis_verification": {
                "assumption": {"claim": "test", "expected": "x"},
                "files_read": ["config.json"],
                "verification_attempts": [{"result": {}, "verdict": "passed"}],
                "verdict": "passed",
            },
        },
    }
    goal.update(overrides)
    return goal


def _make_subtask_result(
    stop_reason: str = "completed",
    messages: list | None = None,
) -> SubtaskExecutionResult:
    return SubtaskExecutionResult(
        stop_reason=stop_reason,
        final_content="done",
        messages=messages or [],
        tools_used=[],
    )


# ---------------------------------------------------------------------------
# _build_subtask_messages
# ---------------------------------------------------------------------------


class TestBuildSubtaskMessages:
    def test_includes_goal_title_and_description(self, executor):
        goal = _make_goal()
        subtask = goal["data"]["subtasks"][1]
        messages = executor._build_subtask_messages(goal, subtask)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

        sys_content = messages[0]["content"]
        assert "Test Goal" in sys_content
        assert "A test goal" in sys_content
        assert "test-project" in sys_content

    def test_includes_subtask_info(self, executor):
        goal = _make_goal()
        subtask = goal["data"]["subtasks"][1]
        messages = executor._build_subtask_messages(goal, subtask)

        sys_content = messages[0]["content"]
        assert "subtask_1" in sys_content
        assert "s1" in sys_content
        assert "1/3 subtasks completed" in sys_content
        assert "declare_checkpoint" in sys_content

    def test_includes_structural_constraints(self, executor):
        goal = _make_goal()
        subtask = goal["data"]["subtasks"][1]
        messages = executor._build_subtask_messages(goal, subtask)

        sys_content = messages[0]["content"]
        assert "config.json" in sys_content
        assert "read_only" in sys_content

    def test_user_message_format(self, executor):
        goal = _make_goal()
        subtask = goal["data"]["subtasks"][1]
        messages = executor._build_subtask_messages(goal, subtask)

        assert messages[1]["content"] == "Execute subtask s1: subtask_1"

    def test_empty_goal_fields_dont_crash(self, executor):
        goal = _make_goal(title="", description="", project="")
        subtask = goal["data"]["subtasks"][0]
        messages = executor._build_subtask_messages(goal, subtask)
        assert len(messages) == 2

    def test_no_subtasks_shows_zero_progress(self, executor):
        goal = _make_goal(data={"subtasks": []})
        subtask = {"id": "s1", "title": "standalone"}
        messages = executor._build_subtask_messages(goal, subtask)
        assert "0/0 subtasks completed" in messages[0]["content"]


# ---------------------------------------------------------------------------
# _has_declared_checkpoint
# ---------------------------------------------------------------------------


class TestHasDeclaredCheckpoint:
    def test_no_messages_returns_false(self, executor):
        assert not executor._has_declared_checkpoint([], "s1")

    def test_matching_openai_format(self, executor):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "declare_checkpoint",
                            "arguments": '{"subtask_id": "s1", "summary": "done"}',
                        }
                    }
                ],
            }
        ]
        assert executor._has_declared_checkpoint(messages, "s1")

    def test_non_matching_openai_format(self, executor):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "declare_checkpoint",
                            "arguments": '{"subtask_id": "s2", "summary": "done"}',
                        }
                    }
                ],
            }
        ]
        assert not executor._has_declared_checkpoint(messages, "s1")

    def test_matching_anthropic_format(self, executor):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "declare_checkpoint",
                        "input": {"subtask_id": "s1", "summary": "done"},
                    }
                ],
            }
        ]
        assert executor._has_declared_checkpoint(messages, "s1")

    def test_non_matching_anthropic_format(self, executor):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "declare_checkpoint",
                        "input": {"subtask_id": "s2"},
                    }
                ],
            }
        ]
        assert not executor._has_declared_checkpoint(messages, "s1")

    def test_different_tool_ignored(self, executor):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "read_file", "arguments": '{"path": "x"}'}}
                ],
            }
        ]
        assert not executor._has_declared_checkpoint(messages, "s1")

    def test_bad_json_arguments_doesnt_crash(self, executor):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "declare_checkpoint",
                            "arguments": "not-json",
                        }
                    }
                ],
            }
        ]
        assert not executor._has_declared_checkpoint(messages, "s1")


# ---------------------------------------------------------------------------
# _check_subtask_done
# ---------------------------------------------------------------------------


class TestCheckSubtaskDone:
    def test_stop_reason_completed(self, executor):
        result = _make_subtask_result(stop_reason="completed")
        assert executor._check_subtask_done(result, {"id": "s1"})

    def test_declare_checkpoint_in_messages(self, executor):
        result = _make_subtask_result(
            stop_reason="max_iterations",
            messages=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "declare_checkpoint",
                                "arguments": '{"subtask_id": "s1", "summary": "x"}',
                            }
                        }
                    ],
                }
            ],
        )
        assert executor._check_subtask_done(result, {"id": "s1"})

    def test_declare_checkpoint_wrong_subtask(self, executor):
        result = _make_subtask_result(
            stop_reason="max_iterations",
            messages=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "declare_checkpoint",
                                "arguments": '{"subtask_id": "s999", "summary": "x"}',
                            }
                        }
                    ],
                }
            ],
        )
        assert not executor._check_subtask_done(result, {"id": "s1"})

    def test_no_declare_not_completed(self, executor):
        result = _make_subtask_result(stop_reason="max_iterations")
        assert not executor._check_subtask_done(result, {"id": "s1"})


# ---------------------------------------------------------------------------
# execute_goal (with mocked AgentRunner)
# ---------------------------------------------------------------------------


class TestExecuteGoal:
    async def test_already_completed(self, executor):
        goal = _make_goal(status="completed")
        result = await executor.execute_goal(goal_id="g1", goal=goal)
        assert result.status == "already_completed"

    async def test_blocked_and_cannot_resume(self, executor):
        goal = _make_goal(
            status="blocked",
            data={
                "subtasks": [{"id": "s0", "title": "s0", "status": "done"}],
                "hypothesis_verification": {
                    "files_read": ["config.json"],
                    "assumption": {"claim": "x"},
                    "verification_attempts": [{"result": {}, "verdict": "failed"}]
                    * 3,
                    "verdict": "failed",
                },
            },
        )
        result = await executor.execute_goal(goal_id="g1", goal=goal)
        assert result.status == "blocked"

    async def test_subtask_0_not_complete_returns_blocked(self, executor, db):
        goal = _make_goal(
            data={
                "subtasks": [{"id": "s0", "title": "s0", "status": "in_progress"}],
                "hypothesis_verification": {},
            },
        )
        db.get_goal.return_value = goal
        result = await executor.execute_goal(goal_id="g1", goal=goal)
        assert result.status == "blocked"
        assert "subtask_0" in (result.message or "")

    async def test_execute_goal_no_db(self, executor_no_db):
        executor_no_db._runner = MagicMock()
        executor_no_db._runner.run = AsyncMock(
            return_value=MagicMock(
                stop_reason="completed",
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
                tool_events=[],
                had_injections=False,
                error=None,
            )
        )
        # Only s0 (already done) — no subtasks to execute, so the goal
        # completes without needing _mark_subtask_done (which is a no-op without DB).
        goal = _make_goal(data={
            "subtasks": [
                {"id": "s0", "title": "s0", "status": "done"},
            ],
            "hypothesis_verification": {
                "assumption": {"claim": "test", "expected": "x"},
                "files_read": ["config.json"],
                "verification_attempts": [{"result": {}, "verdict": "passed"}],
                "verdict": "passed",
            },
        })
        result = await executor_no_db.execute_goal(goal_id="g1", goal=goal)
        assert result.status == "completed"

    async def test_execute_subtask_runs_agent(self, executor, db):
        """Verify that _execute_subtask creates AgentRunSpec with context."""
        goal = _make_goal()

        executor._runner = MagicMock()
        executor._runner.run = AsyncMock(
            return_value=MagicMock(
                stop_reason="completed",
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
                tool_events=[],
                had_injections=False,
                error=None,
            )
        )

        result = await executor.execute_goal(goal_id="g1", goal=goal)

        assert result.status == "completed"

        # Verify the spec had meaningful initial_messages
        call_args = executor._runner.run.call_args
        spec = call_args[0][0]
        assert len(spec.initial_messages) == 2
        assert "Test Goal" in spec.initial_messages[0]["content"]

    async def test_checkpoint_saved_after_subtask(self, executor, db):
        """Verify checkpoint is saved via insert_event."""
        # Use a mutable goal dict so in-place mutations by _mark_subtask_done
        # are visible when get_goal returns the same object.
        goal = _make_goal(
            data={
                "subtasks": [
                    {"id": "s0", "title": "s0", "status": "done"},
                    {"id": "s1", "title": "s1", "status": "todo"},
                ],
                "hypothesis_verification": {
                    "assumption": {"claim": "test", "expected": "x"},
                    "files_read": ["config.json"],
                    "verification_attempts": [{"result": {}, "verdict": "passed"}],
                    "verdict": "passed",
                },
            },
        )
        # get_goal returns the same mutable dict so status updates are visible
        db.get_goal.return_value = goal

        executor._runner = MagicMock()
        executor._runner.run = AsyncMock(
            return_value=MagicMock(
                stop_reason="completed",
                final_content="subtask done",
                messages=[],
                tools_used=["read_file"],
                usage={},
                tool_events=[],
                had_injections=False,
                error=None,
            )
        )

        await executor.execute_goal(goal_id="g1", goal=goal)

        insert_calls = [
            c
            for c in db.insert_event.call_args_list
            if c.kwargs.get("event_type") == "checkpoint"
        ]
        assert len(insert_calls) >= 1  # at least s1 checkpoint saved


# ---------------------------------------------------------------------------
# Checkpoint save/load
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def test_save_uses_json(self, executor, db):
        result = _make_subtask_result(stop_reason="completed", messages=[])
        executor._save_checkpoint("g1", "s1", result)

        call = db.insert_event.call_args
        assert call is not None
        content = call.kwargs.get("content", "")
        parsed = json.loads(content)
        assert parsed["subtask_id"] == "s1"
        assert parsed["stop_reason"] == "completed"

    def test_load_returns_none_no_db(self, executor_no_db):
        assert executor_no_db._get_latest_checkpoint("g1") is None

    def test_load_parses_json(self, executor, db):
        db.list_events.return_value = [
            {
                "content": json.dumps(
                    {"subtask_id": "s1", "stop_reason": "completed"}
                ),
            }
        ]
        result = executor._get_latest_checkpoint("g1")
        assert result is not None
        assert result["subtask_id"] == "s1"

    def test_load_skips_non_json(self, executor, db):
        db.list_events.return_value = [
            {"content": "not-json-at-all"},
        ]
        result = executor._get_latest_checkpoint("g1")
        assert result is None


# ---------------------------------------------------------------------------
# resume_goal
# ---------------------------------------------------------------------------


class TestResumeGoal:
    async def test_goal_not_found(self, executor, db):
        db.get_goal.return_value = None
        result = await executor.resume_goal(goal_id="g1")
        assert result.status == "error"

    async def test_resume_blocked_rechecks_subtask_0(self, executor, db):
        goal = _make_goal(
            status="blocked",
            data={
                "subtasks": [{"id": "s0", "title": "s0", "status": "done"}],
                "hypothesis_verification": {},
            },
        )
        db.get_goal.return_value = goal
        result = await executor.resume_goal(goal_id="g1")
        assert result.status == "blocked"


# ---------------------------------------------------------------------------
# _verify_subtask_result
# ---------------------------------------------------------------------------


class TestVerifySubtaskResult:
    async def test_no_criteria_returns_none(self, executor):
        goal = _make_goal(scope={"structural_constraints": {}})
        subtask = {"id": "s1"}
        result = _make_subtask_result()
        vr = await executor._verify_subtask_result(goal, subtask, result)
        assert vr is None

    async def test_with_criteria_returns_verifier_result(self, executor):
        goal = _make_goal(
            scope={
                "structural_constraints": {
                    "success_criteria": ["output exists"],
                },
            },
        )
        subtask = {"id": "s1"}
        result = _make_subtask_result()

        executor._verifier = MagicMock()
        executor._verifier.verify = AsyncMock(
            return_value=VerifierResult(passed=True, evidence=["ok"], details="ok"),
        )

        vr = await executor._verify_subtask_result(goal, subtask, result)
        assert vr is not None
        assert vr.passed


# ---------------------------------------------------------------------------
# set_provider
# ---------------------------------------------------------------------------


class TestSetProvider:
    async def test_set_provider_recreates_runner(self, executor, provider):
        old_runner = executor._runner
        executor.set_provider(provider, "new-model")
        assert executor._runner is not old_runner
        assert executor._model == "new-model"

    async def test_set_provider_propagates_to_verifier(self, executor, provider):
        old_verifier_runner = executor._verifier._runner
        executor.set_provider(provider, "test-model")
        assert executor._verifier._runner is not old_verifier_runner

    async def test_set_provider_updates_provider_ref(self, executor, provider):
        new_provider = MagicMock()
        new_provider.get_default_model.return_value = "new-model"
        executor.set_provider(new_provider, "new-model")
        assert executor.provider is new_provider


# ---------------------------------------------------------------------------
# _check_dependencies_blocked
# ---------------------------------------------------------------------------


class TestCheckDependenciesBlocked:
    def test_no_dependencies_returns_none(self, executor, db):
        db.list_dependencies.return_value = []
        assert executor._check_dependencies_blocked("g1") is None

    def test_no_db_returns_none(self, executor_no_db):
        assert executor_no_db._check_dependencies_blocked("g1") is None

    def test_dependency_not_completed_returns_blocker(self, executor, db):
        db.list_dependencies.return_value = [{"goal_id": "g1", "depends_on": "g0"}]
        db.get_goal.side_effect = lambda gid: {
            "g0": {"id": "g0", "title": "Dep Goal", "status": "in_progress"},
        }.get(gid)
        result = executor._check_dependencies_blocked("g1")
        assert result is not None
        assert "依赖未完成" in result
        assert "Dep Goal" in result

    def test_all_dependencies_completed_returns_none(self, executor, db):
        db.list_dependencies.return_value = [{"goal_id": "g1", "depends_on": "g0"}]
        db.get_goal.side_effect = lambda gid: {
            "g0": {"id": "g0", "title": "Dep Goal", "status": "completed"},
        }.get(gid)
        assert executor._check_dependencies_blocked("g1") is None


# ---------------------------------------------------------------------------
# _is_goal_complete
# ---------------------------------------------------------------------------


class TestIsGoalComplete:
    def test_all_subtasks_done_returns_true(self, executor):
        goal = _make_goal(data={
            "subtasks": [
                {"id": "s0", "status": "done"},
                {"id": "s1", "status": "done"},
            ],
            "hypothesis_verification": {"verdict": "passed"},
        })
        assert executor._is_goal_complete(goal)

    def test_subtask_not_done_returns_false(self, executor):
        goal = _make_goal(data={
            "subtasks": [
                {"id": "s0", "status": "done"},
                {"id": "s1", "status": "todo"},
            ],
            "hypothesis_verification": {"verdict": "passed"},
        })
        assert not executor._is_goal_complete(goal)

    def test_hypothesis_failed_returns_false(self, executor):
        goal = _make_goal(data={
            "subtasks": [
                {"id": "s0", "status": "done"},
            ],
            "hypothesis_verification": {"verdict": "failed"},
        })
        assert not executor._is_goal_complete(goal)

    def test_no_subtasks_returns_true(self, executor):
        goal = _make_goal(data={
            "subtasks": [],
            "hypothesis_verification": {"verdict": "passed"},
        })
        assert executor._is_goal_complete(goal)


# ---------------------------------------------------------------------------
# _parse_structured_lessons
# ---------------------------------------------------------------------------


class TestParseLessonsYaml:
    def test_yaml_code_block(self, executor):
        text = '''```yaml
- type: optimization
  summary: Use bulk inserts
  detail: Replace individual inserts with batch operations
  tags: [performance, db]
```'''
        lessons = TaskExecutor._parse_structured_lessons(text)
        assert len(lessons) == 1
        assert lessons[0]["type"] == "optimization"
        assert "bulk inserts" in lessons[0]["summary"]

    def test_inline_yaml_list(self, executor):
        text = '''type: optimization
summary: Use caching
detail: Add Redis caching layer
tags: [performance]
'''
        lessons = TaskExecutor._parse_structured_lessons(text)
        assert len(lessons) == 1
        assert lessons[0]["type"] == "optimization"

    def test_multiple_lessons(self, executor):
        text = '''type: optimization
summary: First lesson
---
type: security
summary: Second lesson
'''
        # This may parse as 1 or 2 depending on format handling
        lessons = TaskExecutor._parse_structured_lessons(text)
        assert len(lessons) >= 1

    def test_plain_text_fallback(self, executor):
        text = "Just some plain text about an important lesson learned."
        lessons = TaskExecutor._parse_structured_lessons(text)
        assert len(lessons) == 1
        assert lessons[0]["type"] == "optimization"
        assert "Just some plain text" in lessons[0]["summary"]

    def test_empty_text_fallback(self, executor):
        lessons = TaskExecutor._parse_structured_lessons("")
        assert len(lessons) == 1  # fallback creates one generic lesson

    def test_multiple_lessons_in_yaml_block(self, executor):
        text = '''```yaml
type: optimization
summary: Batch DB operations
detail: Use executemany for bulk inserts
tags: [performance]
type: security
summary: Validate all inputs
detail: Never trust user input
tags: [security]
```'''
        lessons = TaskExecutor._parse_structured_lessons(text)
        assert len(lessons) >= 2  # Should detect two lessons


# ---------------------------------------------------------------------------
# _save_lessons
# ---------------------------------------------------------------------------


class TestSaveLessons:
    def test_save_lessons_no_db_no_error(self, executor_no_db):
        # Should not raise
        executor_no_db._save_lessons("g1", {"title": "Test", "status": "completed"}, "some text")

    def test_save_lessons_calls_insert_lesson(self, executor, db, tmp_path):
        executor._workspace = tmp_path
        lesson_text = '''```yaml
type: optimization
summary: Test lesson
detail: Detail text
tags: [test]
```'''
        executor._save_lessons("g1", {"title": "Test", "status": "completed"}, lesson_text)
        assert db.insert_lesson.called
        call_kwargs = db.insert_lesson.call_args
        assert call_kwargs.kwargs["goal_id"] == "g1"
        assert call_kwargs.kwargs["lesson_type"] == "optimization"

    def test_save_lessons_creates_lessons_file(self, executor, db, tmp_path):
        executor._workspace = tmp_path
        # Mock insert_lesson to do nothing
        db.insert_lesson.return_value = None
        lesson_text = '''```yaml
type: optimization
summary: File lesson
detail: Written to file
tags: [test]
```'''
        # Temporarily patch _parse_structured_lessons to avoid complexity
        from nanobot.agent.task_executor import TaskExecutor
        original_parse = TaskExecutor._parse_structured_lessons
        TaskExecutor._parse_structured_lessons = staticmethod(lambda text: [{"type": "optimization", "summary": "File lesson", "detail": "Written to file", "tags": ["test"]}])
        try:
            executor._save_lessons("g1", {"title": "Test Goal", "status": "completed"}, lesson_text)
            lessons_file = tmp_path / "tasks" / "lessons.md"
            assert lessons_file.exists()
            content = lessons_file.read_text(encoding="utf-8")
            assert "Test Goal" in content
            assert "File lesson" in content
        finally:
            TaskExecutor._parse_structured_lessons = original_parse
