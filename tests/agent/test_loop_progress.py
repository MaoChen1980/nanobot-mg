"""Tests for structured tool-event progress metadata emitted by AgentLoop."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.command import CommandRouter, register_builtin_commands
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path, *, observe_tool: bool = True) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.commands = CommandRouter()
    register_builtin_commands(loop.commands)
    # Enable observe toggles for tests that check tool-event progress
    # Both None (direct calls) and "cli:direct" (default session_key) keys
    loop._session_observe["_observe_think"][None] = observe_tool
    loop._session_observe["_observe_think"]["cli:direct"] = observe_tool
    loop._session_observe["_observe_tool"][None] = observe_tool
    loop._session_observe["_observe_tool"]["cli:direct"] = observe_tool
    return loop


