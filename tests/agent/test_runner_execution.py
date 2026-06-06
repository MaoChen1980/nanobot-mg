"""Tests for sequential tool execution logic.

Covers :mod:`nanobot.agent.runner_execution` — pure ``partition_tool_batches``,
async ``_run_tool`` and ``execute_tools`` with mocked spec.
"""

from __future__ import annotations

from asyncio import CancelledError
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.runner_execution import (
    _run_tool,
    execute_tools,
    partition_tool_batches,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_tool_call(
    name: str = "test_tool",
    arguments: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call_{name}",
        name=name,
        arguments=arguments or {},
    )


def _make_spec(
    fail_on_tool_error: bool = False,
    session_key: str = "test-session",
    injection_callback=None,
) -> MagicMock:
    spec = MagicMock()
    spec.fail_on_tool_error = fail_on_tool_error
    spec.session_key = session_key
    spec.tools = AsyncMock()
    spec.injection_callback = injection_callback
    return spec


def _make_self_ref() -> MagicMock:
    ref = MagicMock()
    ref._log_tool_call = MagicMock()
    return ref


# ===========================================================================
# partition_tool_batches
# ===========================================================================

class TestPartitionToolBatches:
    """``partition_tool_batches`` — pure, one tool per batch."""

    def test_single_call(self):
        calls = [_make_tool_call("read")]
        result = partition_tool_batches(None, calls)
        assert len(result) == 1
        assert result[0] == [calls[0]]

    def test_multiple_calls(self):
        calls = [_make_tool_call("a"), _make_tool_call("b"), _make_tool_call("c")]
        result = partition_tool_batches(None, calls)
        assert len(result) == 3
        assert result[0] == [calls[0]]
        assert result[1] == [calls[1]]
        assert result[2] == [calls[2]]

    def test_empty_list(self):
        result = partition_tool_batches(None, [])
        assert result == []


# ===========================================================================
# _run_tool
# ===========================================================================

class TestRunTool:
    """``_run_tool`` — single tool execution with mocked spec."""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        spec.tools.execute = AsyncMock(return_value="result ok")
        tc = _make_tool_call("my_tool", {"x": 1})

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert result == "result ok"
        assert event["status"] == "ok"
        assert error is None

    @pytest.mark.asyncio
    async def test_repeated_lookup_blocked(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=True)
        tc = _make_tool_call("web_search", {"q": "test"})

        with patch(
            "nanobot.agent.runner_execution.check_repeated_external_lookup",
            return_value="Error: repeated external lookup blocked.",
        ):
            result, event, error = await _run_tool(
                self_ref, spec, tc, {"web_search({'q': 'test'})": 5}, 0, 0,
            )
        assert "repeated" in result
        assert event["status"] == "error"
        assert isinstance(error, RuntimeError)

    @pytest.mark.asyncio
    async def test_repeated_lookup_not_blocked_when_fail_off(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=False)
        tc = _make_tool_call("web_search", {"q": "test"})

        with patch(
            "nanobot.agent.runner_execution.check_repeated_external_lookup",
            return_value="Error: repeated external lookup blocked.",
        ):
            result, event, error = await _run_tool(
                self_ref, spec, tc, {}, 0, 0,
            )
        assert "repeated" in result
        assert event["status"] == "error"
        assert error is None  # no RuntimeError when fail_on_tool_error=False

    @pytest.mark.asyncio
    async def test_prepare_call_success(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        tool_mock = AsyncMock()
        tool_mock.execute = AsyncMock(return_value="prepared result")

        def prepare_call(name: str, args: dict):
            return (tool_mock, args, None)

        spec.tools.prepare_call = prepare_call
        tc = _make_tool_call("my_tool", {"x": 1})

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert result == "prepared result"
        assert event["status"] == "ok"

    @pytest.mark.asyncio
    async def test_prepare_call_error_with_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=True)

        def prepare_call(name: str, args: dict):
            return (None, args, "Error: invalid args")

        spec.tools.prepare_call = prepare_call
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "invalid args" in result
        assert isinstance(error, RuntimeError)

    @pytest.mark.asyncio
    async def test_prepare_call_error_without_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=False)

        def prepare_call(name: str, args: dict):
            return (None, args, "Error: invalid args")

        spec.tools.prepare_call = prepare_call
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "invalid args" in result
        assert error is None

    @pytest.mark.asyncio
    async def test_execution_error_with_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=True)
        spec.tools.execute = AsyncMock(side_effect=ValueError("tool crashed"))
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "ValueError" in result
        assert isinstance(error, RuntimeError)

    @pytest.mark.asyncio
    async def test_execution_error_without_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=False)
        spec.tools.execute = AsyncMock(side_effect=ValueError("tool crashed"))
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "ValueError" in result
        assert error is None

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        spec.tools.execute = AsyncMock(side_effect=CancelledError())
        tc = _make_tool_call("my_tool")

        with pytest.raises(CancelledError):
            await _run_tool(self_ref, spec, tc, {}, 0, 0)

    @pytest.mark.asyncio
    async def test_tool_returns_error_string_with_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=True)
        spec.tools.execute = AsyncMock(return_value="Error: something failed")
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "Error" in result
        assert event["status"] == "error"
        assert isinstance(error, RuntimeError)

    @pytest.mark.asyncio
    async def test_tool_returns_error_string_without_fail(self):
        self_ref = _make_self_ref()
        spec = _make_spec(fail_on_tool_error=False)
        spec.tools.execute = AsyncMock(return_value="Error: something failed")
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert "Error" in result
        assert event["status"] == "error"
        assert error is None

    @pytest.mark.asyncio
    async def test_empty_result_detail_replaced(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        spec.tools.execute = AsyncMock(return_value=None)
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert result is None
        assert event["detail"] == "(empty)"

    @pytest.mark.asyncio
    async def test_long_result_detail_truncated(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        spec.tools.execute = AsyncMock(return_value="x" * 200)
        tc = _make_tool_call("my_tool")

        result, event, error = await _run_tool(
            self_ref, spec, tc, {}, 0, 0,
        )
        assert len(event["detail"]) == 123  # 120 + "..."


# ===========================================================================
# execute_tools
# ===========================================================================

class TestExecuteTools:
    """``execute_tools`` — multi-tool orchestration with mocked _run_tool."""

    @pytest.mark.asyncio
    async def test_executes_all_tools(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        calls = [_make_tool_call("a"), _make_tool_call("b")]

        with patch(
            "nanobot.agent.runner_execution._run_tool",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ("ok", {"name": "t", "status": "ok"}, None)
            results, events, fatal_error, interrupted, cycles, count, injections = (
                await execute_tools(self_ref, spec, calls, {}, [], 0, 0)
            )
        assert count == 2
        assert len(results) == 2
        assert fatal_error is None
        assert not interrupted
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self):
        self_ref = _make_self_ref()
        spec = _make_spec()

        results, events, fatal_error, interrupted, cycles, count, injections = (
            await execute_tools(self_ref, spec, [], {}, [], 0, 0)
        )
        assert count == 0
        assert results == []
        assert not interrupted

    @pytest.mark.asyncio
    async def test_interrupted_on_runtime_error(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        calls = [_make_tool_call("a"), _make_tool_call("b")]

        with patch(
            "nanobot.agent.runner_execution._run_tool",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ("err", {"name": "t", "status": "error"}, RuntimeError("fail"))

            results, events, fatal_error, interrupted, cycles, count, injections = (
                await execute_tools(self_ref, spec, calls, {}, [], 0, 0)
            )
        assert interrupted
        assert count == 1  # stopped after first tool

    @pytest.mark.asyncio
    async def test_fatal_error_from_multiple_results(self):
        self_ref = _make_self_ref()
        spec = _make_spec()
        calls = [_make_tool_call("a")]

        with patch(
            "nanobot.agent.runner_execution._run_tool",
            new_callable=AsyncMock,
            return_value=("err", {"name": "t", "status": "error"}, RuntimeError("fail")),
        ):
            results, events, fatal_error, interrupted, cycles, count, injections = (
                await execute_tools(self_ref, spec, calls, {}, [], 0, 0)
            )
        assert isinstance(fatal_error, RuntimeError)

    @pytest.mark.asyncio
    async def test_injection_draining(self):
        self_ref = _make_self_ref()
        spec = _make_spec(
            injection_callback=AsyncMock(return_value=[{"role": "user", "content": "hi"}])
        )
        calls = [_make_tool_call("a")]

        with patch(
            "nanobot.agent.runner_execution._run_tool",
            new_callable=AsyncMock,
            return_value=("ok", {"name": "t", "status": "ok"}, None),
        ):
            results, events, fatal_error, interrupted, cycles, count, injections = (
                await execute_tools(self_ref, spec, calls, {}, [], 0, 0)
            )
        assert interrupted  # injection triggered interruption
        assert len(injections) == 1
        assert injections[0]["content"] == "hi"
