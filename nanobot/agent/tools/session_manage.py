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
                "description": "Action: 'list' (show all messages with sizes) or 'compress' (replace a message's content with a summary)",
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
            "管理会话上下文 — 查看消息列表、压缩已处理的长内容。\n\n"
            "**核心价值**: 唯一能操控上下文空间的工具。exec/read_file 做不到。\n\n"
            "**什么时候必须用**:\n"
            "- 上下文消息 > 30 条 → 用 list 审计，看哪些可以压缩\n"
            "- 单条工具结果 > 2000 字且已处理完 → compress，只剩摘要\n"
            "- 历史对话 > 50 轮 → 压缩早期工具结果\n"
            "- 不知道当前上下文状态 → 用 list 查看\n\n"
            "**action=compress**: 把一段已处理完的长内容替换成几行摘要\n"
            "  - message_id: 要压缩的消息 ID（必填）\n"
            "  - compress_summary: 替换用的摘要（必填）\n\n"
            "**action=list**: 查看当前所有消息及大小"
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
