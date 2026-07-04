"""Tests for tool schema generation (p(), build_parameters_schema, @tool_parameters).

Catches the silent-discard bug where ``required`` passed to ``@tool_parameters``
was ignored when ``build_parameters_schema()`` was used as a positional argument.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


# ---------------------------------------------------------------------------
# p() helper
# ---------------------------------------------------------------------------

class TestP:
    def test_basic_string(self):
        prop = p("string", "A file path")
        assert prop == {"type": "string", "description": "A file path"}

    def test_with_enum(self):
        prop = p("string", "Priority", enum=["info", "suggestion", "blocker"])
        assert prop["enum"] == ["info", "suggestion", "blocker"]

    def test_with_default_and_maximum(self):
        prop = p("integer", "Count", default=5, maximum=10, minimum=1)
        assert prop["default"] == 5
        assert prop["maximum"] == 10
        assert prop["minimum"] == 1

    def test_with_items_for_array(self):
        prop = p("array", "Items", items=p("string", "An item"))
        assert prop["items"]["type"] == "string"

    def test_empty_description_filled(self):
        prop = p("string")
        assert "description" in prop


# ---------------------------------------------------------------------------
# build_parameters_schema
# ---------------------------------------------------------------------------

class TestBuildParametersSchema:
    def test_with_required(self):
        schema = build_parameters_schema(
            foo=p("string", "foo"),
            required=["foo"],
        )
        assert schema["required"] == ["foo"]
        assert "foo" in schema["properties"]

    def test_without_required(self):
        schema = build_parameters_schema(
            foo=p("string", "foo"),
        )
        assert "required" not in schema

    def test_multiple_required(self):
        schema = build_parameters_schema(
            path=p("string", "path"),
            message=p("string", "msg"),
            required=["path", "message"],
        )
        assert schema["required"] == ["path", "message"]

    def test_with_description(self):
        schema = build_parameters_schema(
            x=p("string", "x"),
            description="A test tool",
        )
        assert schema["description"] == "A test tool"


# ---------------------------------------------------------------------------
# @tool_parameters decorator
# ---------------------------------------------------------------------------

class TestToolParameters:
    """Verifies that @tool_parameters correctly propagates required."""

    def test_positional_schema_with_required(self):
        """Positional schema with required inside: should work."""
        schema = build_parameters_schema(
            foo=p("string", "foo"),
            required=["foo"],
        )
        @tool_parameters(schema)
        class MyTool(Tool):
            async def execute(self, foo: str, **kwargs: Any) -> str:
                return foo

        assert MyTool._tool_parameters_schema["required"] == ["foo"]

    def test_keyword_properties_with_required(self):
        """Keyword-based: properties= + required= should work."""
        @tool_parameters(
            properties={"foo": p("string", "foo")},
            required=["foo"],
        )
        class MyTool(Tool):
            async def execute(self, foo: str, **kwargs: Any) -> str:
                return foo

        assert MyTool._tool_parameters_schema["required"] == ["foo"]

    def test_positional_schema_ignore_inline_required(self):
        """Keyword required is ignored when positional schema is given.

        This documents existing behaviour: when a full schema dict is given
        positionally, the decorator assumes required is already inside it.
        """
        schema = build_parameters_schema(
            foo=p("string", "foo"),
        )
        @tool_parameters(schema, required=["foo"])
        class MyTool(Tool):
            async def execute(self, foo: str, **kwargs: Any) -> str:
                return foo

        assert "required" not in MyTool._tool_parameters_schema

    def test_positional_bare_dict_with_required(self):
        """Bare dict with required inside: works correctly."""
        @tool_parameters({
            "type": "object",
            "properties": {"foo": {"type": "string", "description": "foo"}},
            "required": ["foo"],
        })
        class MyTool(Tool):
            async def execute(self, foo: str, **kwargs: Any) -> str:
                return foo

        assert MyTool._tool_parameters_schema["required"] == ["foo"]

    def test_auto_minlength_for_required_strings(self):
        """Required string params with no minLength get auto-injected minLength:1."""
        @tool_parameters(
            properties={"name": p("string", "Name")},
            required=["name"],
        )
        class MyTool(Tool):
            async def execute(self, name: str, **kwargs: Any) -> str:
                return name

        prop = MyTool._tool_parameters_schema["properties"]["name"]
        assert prop.get("minLength") == 1

    def test_auto_minlength_not_overwritten(self):
        """If minLength is already set, it's not overwritten."""
        @tool_parameters(
            properties={"name": p("string", "Name", minLength=3)},
            required=["name"],
        )
        class MyTool(Tool):
            async def execute(self, name: str, **kwargs: Any) -> str:
                return name

        prop = MyTool._tool_parameters_schema["properties"]["name"]
        assert prop.get("minLength") == 3


# ---------------------------------------------------------------------------
# Contract: schema defaults match execute() for recently-modified tools
# ---------------------------------------------------------------------------

class TestSpecificSchemaDefaults:
    """Verify that schema defaults match execute() defaults for modified tools."""

    def test_web_search_count_default(self):
        from nanobot.agent.tools.web import WebSearchTool
        schema = WebSearchTool().parameters
        assert schema["properties"]["count"]["default"] == 5
        assert schema["properties"]["count"]["maximum"] == 10

    def test_tool_call_log_limit_default(self):
        from nanobot.agent.tools.tool_call_log import ToolCallLogTool
        from nanobot.agent.db import NanobotDB
        import tempfile, os
        path = os.path.join(tempfile.gettempdir(), "nanobot_schema_test.db")
        try:
            db = NanobotDB(db_path=path)
            tool = ToolCallLogTool(db=db)
            schema = tool._tool_parameters_schema
            assert schema["properties"]["limit"]["default"] == 20
            assert schema["properties"]["limit"]["maximum"] == 100
            db.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_send_message_priority_enum(self):
        # SendMessageTool does not have a priority parameter in its schema.
        # The tool accepts message priority via kwargs but it is not part of
        # the LLM-facing parameter schema.
        from nanobot.agent.tools.send_message import SendMessageTool
        from unittest.mock import MagicMock
        tool = SendMessageTool(manager=MagicMock())
        schema = tool._tool_parameters_schema
        # priority is not a declared schema parameter (it goes via **kwargs)
        assert "priority" not in schema["properties"]

    def test_notify_orchestrator_priority_enum(self):
        from nanobot.agent.tools.notify_orchestrator import NotifyOrchestratorTool
        schema = NotifyOrchestratorTool._tool_parameters_schema
        assert schema["properties"]["priority"]["enum"] == ["info", "suggestion", "blocker"]

    def test_checkpoint_message_not_required(self):
        from nanobot.agent.tools.checkpoint import SaveCheckpointTool
        schema = SaveCheckpointTool._tool_parameters_schema
        assert "message" not in schema.get("required", [])
        assert "path" in schema.get("required", [])

    def test_grep_offset_default(self):
        from nanobot.agent.tools.search import GrepTool
        schema = GrepTool().parameters
        assert schema["properties"]["offset"]["default"] == 0

    def test_semantic_search_required(self):
        from nanobot.agent.tools.semantic_search import SearchTextTool
        schema = SearchTextTool._tool_parameters_schema
        assert "query" in schema.get("required", [])

    def test_message_tool_has_message_id(self):
        from nanobot.agent.tools.message import MessageTool
        schema = MessageTool._tool_parameters_schema
        assert "message_id" in schema["properties"]
        assert schema["properties"]["message_id"]["type"] == "string"
        assert "content" in schema.get("required", [])

    def test_web_fetch_max_chars_default(self):
        from nanobot.agent.tools.web import WebFetchTool
        schema = WebFetchTool._tool_parameters_schema
        assert schema["properties"]["max_chars"]["default"] == 100000
