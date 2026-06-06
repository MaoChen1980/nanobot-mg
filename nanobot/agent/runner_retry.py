"""Retry/backoff utilities for agent loop."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable


# ---------------------------------------------------------------------------
# Backoff configuration
# ---------------------------------------------------------------------------


@dataclass
class BackoffConfig:
    """Exponential backoff configuration."""

    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.1


class BackoffStrategy:
    """Backoff strategy type constants."""
    EXPONENTIAL = "exponential"
    FIXED = "fixed"
    LINEAR = "linear"


# ---------------------------------------------------------------------------
# Retry state tracking
# ---------------------------------------------------------------------------


@dataclass
class RetryState:
    """Per-category retry attempt tracking."""

    category: str
    attempts: int = 0
    last_attempt: str | None = None
    success: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    def record_attempt(self, detail: str = "") -> None:
        self.attempts += 1
        self.last_attempt = detail
        self.history.append({
            "attempt": self.attempts,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        })

    def record_success(self) -> None:
        self.success = True


# ---------------------------------------------------------------------------
# Retry context — holds state for all retry categories
# ---------------------------------------------------------------------------


class RetryContext:
    """Collects retry state for empty-response, length-recovery, LLM errors, and tool-name content detection."""

    def __init__(self) -> None:
        self.empty_response_state = RetryState("empty_response")
        self.length_recovery_state = RetryState("length_recovery")
        self.llm_request_state = RetryState("llm_request")
        self.tool_name_content_state = RetryState("tool_name_content")

    def _get_state(self, category: str) -> RetryState:
        if category == "empty_response":
            return self.empty_response_state
        if category == "length_recovery":
            return self.length_recovery_state
        if category == "llm_request":
            return self.llm_request_state
        if category == "tool_name_content":
            return self.tool_name_content_state
        raise ValueError(f"Unknown retry category: {category}")

    async def wait_with_backoff(
        self,
        category: str,
        *,
        retry_callback: Callable[[str], Awaitable[None]] | None = None,
        config: BackoffConfig | None = None,
    ) -> None:
        """Wait with exponential backoff for the given retry category."""
        state = self._get_state(category)
        cfg = config or BackoffConfig()

        base_delay = cfg.initial_delay * (cfg.multiplier ** max(0, state.attempts - 1))
        capped = min(base_delay, cfg.max_delay)
        jitter_range = capped * cfg.jitter
        delay = max(0.1, capped + random.uniform(-jitter_range, jitter_range))

        if retry_callback:
            await retry_callback(category)

        await asyncio.sleep(delay)

    def summary(self) -> dict[str, Any]:
        return {
            "empty_response": {
                "attempts": self.empty_response_state.attempts,
                "success": self.empty_response_state.success,
                "last_attempt": self.empty_response_state.last_attempt,
            },
            "length_recovery": {
                "attempts": self.length_recovery_state.attempts,
                "success": self.length_recovery_state.success,
                "last_attempt": self.length_recovery_state.last_attempt,
            },
            "llm_request": {
                "attempts": self.llm_request_state.attempts,
                "success": self.llm_request_state.success,
                "last_attempt": self.llm_request_state.last_attempt,
            },
            "tool_name_content": {
                "attempts": self.tool_name_content_state.attempts,
                "success": self.tool_name_content_state.success,
                "last_attempt": self.tool_name_content_state.last_attempt,
            },
        }
