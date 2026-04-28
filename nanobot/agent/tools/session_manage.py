"""Session management tool: let LLM manage its own session messages."""

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters


if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "exclude", "compress", "archive", "auto_clean"],
                "description": "What action to perform",
            },
            "message_id": {
                "type": "string",
                "description": "Message id to act on (e.g. msg_001)",
            },
            "compress_summary": {
                "type": "string",
                "description": "Summary to replace the message content with (for compress action)",
            },
            "size_threshold": {
                "type": "integer",
                "description": "Minimum size in chars to consider bloated (for auto_clean action, default 5000)",
            },
        },
        "required": ["action"],
    }
)
class SessionManageTool(Tool):
    """Let LLM manage its own session messages.

    Actions:
    - list: show all messages with id, role, size, status
    - exclude: mark a message as excluded (won't enter context)
    - compress: replace message content with your summary
    - archive: move to persistent history
    - auto_clean: batch-exclude all non-user messages exceeding size_threshold
    """

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    @property
    def name(self) -> str:
        return "session_manage"

    @property
    def description(self) -> str:
        return """Your context gets polluted with bloated tool results you don't need.

Use session_manage when:
- A tool result was large (>5KB) and you're done processing it
- You called read_file on a persisted full output → compress it after
- Context feels heavy or >70% full → audit and exclude aggressively
- You want to see what's taking up space → call list

Actions:
- list: see all messages with id, role, size, status
- exclude: remove from context (won't affect history)
- compress: replace with your summary of key points
- archive: move to persistent storage
- auto_clean: batch-exclude all non-user messages larger than size_threshold (default 5000). Use when context is bloated and you don't want to manually pick IDs.

Without this tool, bloated tool results accumulate forever and starve your context budget."""

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        action: str,
        message_id: str | None = None,
        compress_summary: str | None = None,
        size_threshold: int = 5000,
        **kwargs: Any,
    ) -> str:
        loop: "AgentLoop" = self._loop
        session_key = getattr(loop, "_current_session_key", None) or "cli:direct"
        session = loop.sessions.get_or_create(session_key)

        if action == "list":
            return self._list_messages(session)
        if action == "exclude":
            if not message_id:
                return "Error: message_id required for exclude"
            return self._exclude_message(session, message_id)
        if action == "compress":
            if not message_id:
                return "Error: message_id required for compress"
            if not compress_summary:
                return "Error: compress_summary required for compress"
            return self._compress_message(session, message_id, compress_summary)
        if action == "archive":
            if not message_id:
                return "Error: message_id required for archive"
            return self._archive_message(session, message_id)
        if action == "auto_clean":
            return self._auto_clean(session, size_threshold)
        return f"Error: Unknown action: {action}"

    def _list_messages(self, session) -> str:
        lines = [f"Session has {len(session.messages)} messages (showing last 50):"]
        lines.append("id | role | size(chars) | status | name")
        for m in session.messages[-50:]:
            mid = m.get("id", "?")
            role = m.get("role", "?")
            content = m.get("content", "")
            size = len(content) if isinstance(content, str) else 0
            status = m.get("status", "active")
            name = m.get("name", "")
            lines.append(f"{mid} | {role} | {size} | {status} | {name}")
        return "\n".join(lines)

    def _exclude_message(self, session, message_id: str) -> str:
        for m in session.messages:
            if m.get("id") == message_id:
                m["status"] = "excluded"
                return f"Excluded {message_id}."
        return f"Error: Message {message_id} not found."

    def _compress_message(self, session, message_id: str, summary: str) -> str:
        for m in session.messages:
            if m.get("id") == message_id:
                m["content"] = f"[compressed]: {summary}"
                m["status"] = "compressed"
                return f"Compressed {message_id}."
        return f"Error: Message {message_id} not found."

    def _archive_message(self, session, message_id: str) -> str:
        for m in session.messages:
            if m.get("id") == message_id:
                m["status"] = "archived"
                return f"Archived {message_id}."
        return f"Error: Message {message_id} not found."

    def _auto_clean(self, session, threshold: int) -> str:
        """Batch-exclude all non-user messages exceeding the size threshold."""
        excluded: list[tuple[str, str, int]] = []  # (id, role, size)
        for m in session.messages:
            content = m.get("content", "")
            size = len(content) if isinstance(content, str) else 0
            role = m.get("role", "?")
            status = m.get("status", "active")
            # Only exclude active, non-user messages above threshold
            if status == "active" and role != "user" and size > threshold:
                m["status"] = "excluded"
                excluded.append((m.get("id", "?"), role, size))
        if not excluded:
            return f"No messages found above {threshold} chars."
        total = sum(s for _, _, s in excluded)
        lines = [f"Auto-cleaned {len(excluded)} messages ({total:,} total chars):"]
        for mid, role, size in excluded:
            lines.append(f"  {mid} | {role} | {size:,} chars")
        return "\n".join(lines)
