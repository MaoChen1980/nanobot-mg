"""Context variables shared across agent modules."""

from contextvars import ContextVar

# Stores the current message list during agent loop execution.
# Subagent tools read this to build their context block.
_current_messages_for_subagent: ContextVar[list | None] = ContextVar(
    "current_messages_for_subagent", default=None
)
