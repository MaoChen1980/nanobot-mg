"""Constants and callback helpers for AgentRunner."""

from __future__ import annotations

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_MAX_INJECTIONS_PER_TURN = 50
_MAX_INJECTION_CYCLES = 20
_MAX_MODEL_ERROR_RETRIES = 1  # Number of times to let LLM retry after content-safety errors
_SNIP_SAFETY_BUFFER = 4096
_BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"

