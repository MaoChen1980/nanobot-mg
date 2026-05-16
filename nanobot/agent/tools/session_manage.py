"""Session management tool: inspect and compress session messages."""

from __future__ import annotations

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
                "enum": ["list", "compress"],
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
    """Inspect and compress session messages."""

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    @property
    def name(self) -> str:
        return "session_manage"

    @property
    def description(self) -> str:
        return (
            "压缩废话：把已经处理完的长内容替换成摘要，省空间。\n\n"
            "**action=compress**: 把一段已经没用的长内容替换成几句话\n"
            "  - message_id: 要压缩的消息 ID（必填）\n"
            "  - compress_summary: 替换用的摘要（必填）\n"
            "  什么时候用：一大段工具输出、日志、代码结果你已经看完了，\n"
            "  不需要保留原文了，压缩成几句话省点位置\n\n"
            "**action=list**: 看看当前有哪些消息"
        )

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
        if action == "compress":
            if not message_id:
                return "Error: message_id required for compress"
            if not compress_summary:
                return "Error: compress_summary required for compress"
            return self._compress_message(session, message_id, compress_summary)
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

    def _compress_message(self, session, message_id: str, summary: str) -> str:
        for m in session.messages:
            if m.get("id") == message_id:
                m["content"] = f"[compressed]: {summary}"
                m["status"] = "compressed"
                return f"Compressed {message_id}."
        return f"Error: Message {message_id} not found."
