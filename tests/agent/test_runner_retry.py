import asyncio
from unittest.mock import AsyncMock

from nanobot.agent.runner_retry import BackoffConfig, BackoffStrategy, RetryState, RetryContext


class TestBackoffConfig:
    def test_defaults(self) -> None:
        cfg = BackoffConfig()
        assert cfg.initial_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.multiplier == 2.0
        assert cfg.jitter == 0.1

    def test_custom_values(self) -> None:
        cfg = BackoffConfig(initial_delay=0.5, max_delay=30.0, multiplier=3.0, jitter=0.2)
        assert cfg.initial_delay == 0.5
        assert cfg.max_delay == 30.0
        assert cfg.multiplier == 3.0
        assert cfg.jitter == 0.2


class TestRetryState:
    def test_initial_state(self) -> None:
        state = RetryState("empty_response")
        assert state.category == "empty_response"
        assert state.attempts == 0
        assert state.last_attempt is None
        assert state.success is False
        assert state.history == []

    def test_record_attempt(self) -> None:
        state = RetryState("llm_request")
        state.record_attempt("timeout error")
        assert state.attempts == 1
        assert state.last_attempt == "timeout error"
        assert len(state.history) == 1
        assert state.history[0]["attempt"] == 1
        assert state.history[0]["detail"] == "timeout error"
        assert "timestamp" in state.history[0]

    def test_record_attempt_multiple(self) -> None:
        state = RetryState("length_recovery")
        state.record_attempt("first")
        state.record_attempt("second")
        assert state.attempts == 2
        assert len(state.history) == 2

    def test_record_success(self) -> None:
        state = RetryState("empty_response")
        assert state.success is False
        state.record_success()
        assert state.success is True


class TestRetryContext:
    def test_init_creates_all_states(self) -> None:
        ctx = RetryContext()
        assert ctx.empty_response_state.category == "empty_response"
        assert ctx.length_recovery_state.category == "length_recovery"
        assert ctx.llm_request_state.category == "llm_request"

    def test_summary_empty(self) -> None:
        ctx = RetryContext()
        s = ctx.summary()
        for cat in ("empty_response", "length_recovery", "llm_request"):
            assert s[cat]["attempts"] == 0
            assert s[cat]["success"] is False
            assert s[cat]["last_attempt"] is None

    def test_summary_after_attempts(self) -> None:
        ctx = RetryContext()
        ctx.empty_response_state.record_attempt("no content")
        ctx.llm_request_state.record_attempt("rate limit")
        ctx.llm_request_state.record_success()

        s = ctx.summary()
        assert s["empty_response"]["attempts"] == 1
        assert s["empty_response"]["last_attempt"] == "no content"
        assert s["llm_request"]["attempts"] == 1
        assert s["llm_request"]["success"] is True
        assert s["length_recovery"]["attempts"] == 0

    def test_get_state_unknown_raises(self) -> None:
        ctx = RetryContext()
        try:
            ctx._get_state("nonexistent")
            assert False, "should have raised"
        except ValueError as e:
            assert "Unknown retry category" in str(e)

    def test_get_state_valid_categories(self) -> None:
        ctx = RetryContext()
        assert ctx._get_state("empty_response") is ctx.empty_response_state
        assert ctx._get_state("length_recovery") is ctx.length_recovery_state
        assert ctx._get_state("llm_request") is ctx.llm_request_state

    async def test_wait_with_backoff_calls_callback(self) -> None:
        ctx = RetryContext()
        callback = AsyncMock()
        await ctx.wait_with_backoff("empty_response", retry_callback=callback)
        callback.assert_awaited_once_with("empty_response")

    async def test_wait_with_backoff_small_delay(self) -> None:
        ctx = RetryContext()
        cfg = BackoffConfig(initial_delay=0.01, max_delay=0.05, jitter=0.0)
        start = asyncio.get_event_loop().time()
        await ctx.wait_with_backoff("empty_response", config=cfg)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.005

    async def test_wait_with_backoff_multiple_attempts_exponential(self) -> None:
        ctx = RetryContext()
        ctx.empty_response_state.record_attempt("first")
        ctx.empty_response_state.record_attempt("second")
        cfg = BackoffConfig(initial_delay=0.01, max_delay=0.1, multiplier=2.0, jitter=0.0)
        start = asyncio.get_event_loop().time()
        await ctx.wait_with_backoff("empty_response", config=cfg)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.015

    async def test_backoff_capped_at_max_delay(self) -> None:
        ctx = RetryContext()
        for _ in range(10):
            ctx.empty_response_state.record_attempt("loop")
        cfg = BackoffConfig(initial_delay=1.0, max_delay=0.05, multiplier=2.0, jitter=0.0)
        start = asyncio.get_event_loop().time()
        await ctx.wait_with_backoff("empty_response", config=cfg)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.5


class TestBackoffStrategy:
    def test_constants(self) -> None:
        assert BackoffStrategy.EXPONENTIAL == "exponential"
        assert BackoffStrategy.FIXED == "fixed"
        assert BackoffStrategy.LINEAR == "linear"
