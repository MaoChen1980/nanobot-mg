"""Constants for AgentLoop."""

from __future__ import annotations

import re

_RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
_PENDING_USER_TURN_KEY = "pending_user_turn"

# Regex for LLM-provided tool result summaries (see tool_result_summary instruction).
# Strip tool_summary markers entirely (tag + content) from user-facing output.
# The summary replaces the tool result in session history, not the assistant's
# visible response.
_SUMMARY_RE = re.compile(
    r'\[tool_summary:([^\]]+)\](.*?)\[/tool_summary\]',
    re.DOTALL,
)

# ----------------------------------------------------------------------
# Retry / backoff defaults
# ----------------------------------------------------------------------
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BACKOFF_INITIAL = 1.0   # seconds
_DEFAULT_RETRY_BACKOFF_MAX = 60.0      # seconds
_DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0
_DEFAULT_RETRY_BACKOFF_JITTER = 0.1

# ----------------------------------------------------------------------
# AssessMe defaults (moved to AgentDefaults schema)
# ----------------------------------------------------------------------