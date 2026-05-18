"""Integration test: AgentRunner logs tool calls when db is provided."""

from __future__ import annotations

import tempfile, os
from unittest.mock import MagicMock, AsyncMock

import pytest

from nanobot.agent.db import NanobotDB
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.gettempdir(), f"nanobot_runner_log_test_{os.getpid()}.db")
    yield p
    try:
        if os.path.exists(p):
            os.remove(p)
    except PermissionError:
        pass  # Windows lock




