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
                "enum": ["list", "exclude", "compress", "archive"],
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
    """

    @property
    def name(self) -> str:
        return "session_manage"

    @property
    def description(self) -> str:
        return """Manage session messages: list, exclude, compress, archive.

Use this to actively control your context. You can:
- list: see all messages in session with their id, role, size, status
- exclude: mark a message as excluded (won't enter context)
- compress: replace message content with your summary
- archive: move to persistent history

Every message has an id (e.g. msg_001). Use the id to refer to it.
"""

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        action: str,
        message_id: str | None = None,
        compress_summary: str | None = None,
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
