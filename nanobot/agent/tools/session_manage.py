"""Session management tool: let LLM manage its own session messages."""

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
        return (
            "**用途**: 管理会话消息，控制上下文空间。\n\n"
            "**限制**:\n"
            "- 只能管理当前会话的消息\n"
            "- exclude 不影响持久化历史，只影响上下文\n\n"
            "**错误应对**:\n"
            "- message_id 不存在 → 返回错误\n"
            "- 缺少必需参数 → 返回具体错误\n\n"
            "**边界条件**:\n"
            "- 工具结果太大（>5KB）且已处理完 → 压缩或排除\n"
            "- 上下文感觉沉重（>70%）→ audit 后激进排除\n"
            "- 手动排除太多 → 用 list 检查后恢复\n\n"
            "**极简案例**: session_manage(action='auto_clean', size_threshold=5000)\n"
            "→ 自动排除所有 >5000 字符的非用户消息"
        )

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
