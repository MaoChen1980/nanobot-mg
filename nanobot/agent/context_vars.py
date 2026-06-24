"""Coroutine-local context for the current agent loop and session."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# The current session_key (set per-request in loop.py).
_current_session_key: ContextVar[str] = ContextVar("_current_session_key", default="")

# The current inbound message (set per-request in loop.py).
_current_inbound: ContextVar[Any] = ContextVar("_current_inbound", default=None)

# The current messages to use for a subagent (set by spawn tool).
_current_messages_for_subagent: ContextVar[list[dict] | None] = ContextVar(
    "_current_messages_for_subagent", default=None
)

# Flag: True during subagent execution. Used by SpawnTool to block nested spawn.
_in_subagent: ContextVar[bool] = ContextVar("_in_subagent", default=False)

# Flag: True when prompt debug is enabled for this session.
_current_debug_enabled: ContextVar[bool] = ContextVar("_current_debug_enabled", default=False)

