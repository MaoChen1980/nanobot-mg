"""Constants for AgentLoop."""

from __future__ import annotations

_RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
_PENDING_USER_TURN_KEY = "pending_user_turn"

# ----------------------------------------------------------------------
# Retry / backoff defaults
# ----------------------------------------------------------------------
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BACKOFF_INITIAL = 1.0   # seconds
_DEFAULT_RETRY_BACKOFF_MAX = 60.0      # seconds
_DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0
_DEFAULT_RETRY_BACKOFF_JITTER = 0.1

# ----------------------------------------------------------------------
# AssessMe defaults
# ----------------------------------------------------------------------
_DEFAULT_ASSESS_INTERVAL = 10  # every N user messages