"""Memory system — re-exports from split modules for backward compatibility."""

from nanobot.utils.helpers import estimate_message_tokens as estimate_message_tokens

from nanobot.agent.memory_store import MemoryStore
from nanobot.agent.memory_consolidator import Consolidator
from nanobot.agent.memory_dream import Dream

# Re-export constants that were previously in this module
from nanobot.agent.memory_consolidator import (
    _RAW_ARCHIVE_MAX_CHARS,
    _ARCHIVE_SUMMARY_MAX_CHARS,
    _HISTORY_ENTRY_HARD_CAP,
)

__all__ = ["MemoryStore", "Consolidator", "Dream", "estimate_message_tokens", "_RAW_ARCHIVE_MAX_CHARS", "_ARCHIVE_SUMMARY_MAX_CHARS", "_HISTORY_ENTRY_HARD_CAP"]
