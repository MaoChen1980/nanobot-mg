from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


class _ContextRecordingTool:
    name = "cron"
    concurrency_safe = False

    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def set_context(
        self,
        channel: str,
        chat_id: str,
        metadata: dict | None = None,
        session_key: str | None = None,
    ) -> None:
        self.contexts.append({
            "channel": channel,
            "chat_id": chat_id,
            "metadata": metadata,
            "session_key": session_key,
        })

    async def execute(self, **_kwargs) -> str:
        return "created"


class _Tools:
    def __init__(self, tool: _ContextRecordingTool) -> None:
        self.tool = tool

    def get(self, name: str):
        return self.tool if name == "cron" else None

    def get_definitions(self) -> list:
        return []

    def prepare_call(self, name: str, arguments: dict):
        return (self.tool, arguments, None) if name == "cron" else (None, arguments, None)


