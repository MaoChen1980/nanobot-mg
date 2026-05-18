"""Tests for VerifierAgent - result verification for subtask outputs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.verify.result import VerifierAgent, VerifierResult


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
def verifier(provider, tools):
    return VerifierAgent(provider=provider, tools=tools, model="test-model")


def _make_goal(**overrides) -> dict:
    """Build a minimal goal dict with success_criteria."""
    goal = {
        "id": "g1",
        "title": "Test Goal",
        "status": "in_progress",
        "scope": {
            "structural_constraints": {
                "success_criteria": ["output file exists", "config is valid"],
            },
        },
        "data": {"subtasks": []},
    }
    goal.update(overrides)
    return goal


def _make_subtask(**overrides) -> dict:
    subtask = {"id": "s1", "title": "write config", "status": "done"}
    subtask.update(overrides)
    return subtask


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_verdict_passed(self, verifier):
        assert verifier._parse_verdict("Verdict: passed. All criteria met.")

    def test_verdict_failed(self, verifier):
        assert not verifier._parse_verdict("Verdict: failed. File not found.")

    def test_all_criteria_met(self, verifier):
        assert verifier._parse_verdict("All criteria met. Everything looks good.")

    def test_criterion_not_met(self, verifier):
        assert not verifier._parse_verdict("criterion not met: missing file")

    def test_failed_keyword(self, verifier):
        assert not verifier._parse_verdict("failed: config file is missing")

    def test_ambiguous_defaults_true(self, verifier):
        assert verifier._parse_verdict("I checked the files.")

    def test_failed_and_passed_ambiguous(self, verifier):
        """When both 'failed' and 'passed' appear, don't assume."""
        assert verifier._parse_verdict("Some tests passed, some failed")


# ---------------------------------------------------------------------------
# verify() with no success_criteria
# ---------------------------------------------------------------------------


class TestVerifyNoCriteria:
    async def test_no_scope_returns_passed(self, verifier):
        goal = {"id": "g1", "data": {}}
        result = await verifier.verify(goal, _make_subtask(), "done", ["read_file"])
        assert result.passed
        assert "No success criteria" in result.details

    async def test_empty_criteria_returns_passed(self, verifier):
        goal = _make_goal(scope={"structural_constraints": {"success_criteria": []}})
        result = await verifier.verify(goal, _make_subtask(), "done", ["read_file"])
        assert result.passed
        assert "No success criteria" in result.details


# ---------------------------------------------------------------------------
# _build_readonly_registry
# ---------------------------------------------------------------------------


class TestBuildReadonlyRegistry:
    def test_filters_to_readonly_tools(self, provider, tools):
        """Verify registry filtering includes read-only tools and excludes others."""
        read_tool = MagicMock()
        read_tool.name = "read_file"
        read_tool.read_only = True

        write_tool = MagicMock()
        write_tool.name = "write_file"
        write_tool.read_only = False

        exec_tool = MagicMock()
        exec_tool.name = "exec"
        exec_tool.read_only = False

        grep_tool = MagicMock()
        grep_tool.name = "grep"
        grep_tool.read_only = True

        tools._tools = {
            "read_file": read_tool,
            "write_file": write_tool,
            "exec": exec_tool,
            "grep": grep_tool,
        }

        v = VerifierAgent(provider=provider, tools=tools, model="test-model")
        reg = v._build_readonly_registry()

        assert reg.has("read_file")
        assert reg.has("grep")
        assert not reg.has("write_file")
        assert not reg.has("exec")


# ---------------------------------------------------------------------------
# verify() with mocked AgentRunner
# ---------------------------------------------------------------------------


class TestVerifyWithRunner:
    async def test_verify_returns_verifier_result(self, verifier):
        """Smoke test: verify returns a VerifierResult."""
        verifier._runner = MagicMock()
        verifier._runner.run = AsyncMock(
            return_value=MagicMock(
                stop_reason="completed",
                final_content="Verdict: passed. All criteria met.",
                messages=[{"role": "assistant", "content": "Verdict: passed."}],
                tools_used=[],
                usage={},
                tool_events=[],
                had_injections=False,
                error=None,
            )
        )
        verifier._tools._tools = {}

        goal = _make_goal()
        subtask = _make_subtask()
        result = await verifier.verify(goal, subtask, "done", ["read_file"])

        assert isinstance(result, VerifierResult)
        assert result.passed is True

    async def test_verify_detects_failure(self, verifier):
        verifier._runner = MagicMock()
        verifier._runner.run = AsyncMock(
            return_value=MagicMock(
                stop_reason="completed",
                final_content="Verdict: failed. Output file not found.",
                messages=[{"role": "assistant", "content": "Verdict: failed."}],
                tools_used=[],
                usage={},
                tool_events=[],
                had_injections=False,
                error=None,
            )
        )
        verifier._tools._tools = {}

        goal = _make_goal()
        subtask = _make_subtask()
        result = await verifier.verify(goal, subtask, "done", ["read_file"])

        assert not result.passed


# ---------------------------------------------------------------------------
# set_provider
# ---------------------------------------------------------------------------


class TestSetProvider:
    def test_set_provider_recreates_runner(self, verifier, provider):
        old_runner = verifier._runner
        verifier.set_provider(provider, "test-model")
        assert verifier._runner is not old_runner

    def test_set_provider_updates_model(self, verifier, provider):
        verifier.set_provider(provider, "new-model")
        assert verifier._model == "new-model"

    def test_set_provider_updates_provider_ref(self, verifier):
        new_provider = MagicMock()
        verifier.set_provider(new_provider, "test-model")
        assert verifier._provider is new_provider


# ---------------------------------------------------------------------------
# _extract_evidence
# ---------------------------------------------------------------------------


class TestExtractEvidence:
    def test_empty_messages_returns_empty_list(self, verifier):
        assert verifier._extract_evidence([]) == []

    def test_extracts_from_assistant_messages(self, verifier):
        messages = [
            {"role": "user", "content": "user message"},
            {"role": "assistant", "content": "Line 1\nLine 2\nLine 3"},
        ]
        evidence = verifier._extract_evidence(messages)
        assert len(evidence) >= 3
        assert "Line 1" in evidence

    def test_limits_to_20_lines(self, verifier):
        messages = [
            {"role": "assistant", "content": "\n".join(f"Line {i}" for i in range(50))},
        ]
        evidence = verifier._extract_evidence(messages)
        assert len(evidence) <= 20

    def test_skips_empty_content(self, verifier):
        messages = [
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Real content"},
        ]
        evidence = verifier._extract_evidence(messages)
        assert "Real content" in evidence
