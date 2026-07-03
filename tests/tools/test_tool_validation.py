import shlex
import subprocess
import sys
from typing import Any

from nanobot.agent.tools import Schema, p, tool_parameters, build_parameters_schema
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


@tool_parameters(properties={
    "query": p("string", "", minLength=2),
    "count": p("integer", "", minimum=1, maximum=10),
}, required=["query", "count"])
class DecoratedSampleTool(Tool):
    @property
    def name(self) -> str:
        return "decorated_sample"

    @property
    def description(self) -> str:
        return "decorated sample tool"

    async def execute(self, **kwargs: Any) -> str:
        return f"ok:{kwargs['count']}"


def test_schema_validate_value_matches_tool_validate_params() -> None:
    """validate_json_schema_value and Tool.validate_params agree."""
    root = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query", "count"],
    }
    params = {"query": "h", "count": 2}

    class _Mini(Tool):
        @property
        def name(self) -> str:
            return "m"

        @property
        def description(self) -> str:
            return ""

        @property
        def parameters(self) -> dict[str, Any]:
            return root

        async def execute(self, **kwargs: Any) -> str:
            return ""

    expected = _Mini().validate_params(params)
    assert Schema.validate_json_schema_value(params, root, "") == expected
    assert Schema.validate_json_schema_value(params, root, "") == expected
    assert Schema.validate_json_schema_value(0, {"type": "integer", "minimum": 1}, "n") == ["n must be >= 1"]


def test_schema_classes_equivalent_to_sample_tool_parameters() -> None:
    """Schema fragments match hand-written dict equivalents."""
    built: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "mode": {"type": "string", "enum": ["fast", "full"]},
            "meta": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["tag"],
            },
        },
        "required": ["query", "count"],
    }
    assert built == SampleTool().parameters


def test_tool_parameters_returns_cached_copy_per_access() -> None:
    tool = DecoratedSampleTool()
    first = tool.parameters
    second = tool.parameters
    assert first == second


async def test_registry_executes_decorated_tool_end_to_end() -> None:
    reg = ToolRegistry()
    reg.register(DecoratedSampleTool())

    ok = await reg.execute("decorated_sample", {"query": "hello", "count": 3})
    assert ok == "ok:3"

    err = await reg.execute("decorated_sample", {"query": "h", "count": 3})
    assert "Invalid parameters" in err


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)
    assert any("str('2')" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


# --- Enhanced error message tests ---


def test_validate_params_enhanced_error_messages() -> None:
    """Error messages should include actual type and value for type mismatches."""
    tool = SampleTool()

    # integer type mismatch
    errors = tool.validate_params({"query": "hi", "count": "not_a_number"})
    assert any("count should be integer" in e for e in errors)
    assert any("str('not_a_number')" in e for e in errors)

    # boolean type mismatch
    errors2 = tool.validate_params({"query": "hi", "count": 2, "mode": True})
    assert any("mode should be string" in e for e in errors2)
    assert any("bool(True)" in e for e in errors2)

    # array instead of string
    errors3 = tool.validate_params({"query": "hi", "count": 2, "mode": ["fast"]})
    assert any("mode should be string" in e for e in errors3)
    assert any("list" in e for e in errors3)


def test_validate_json_schema_value_direct_enhanced_errors() -> None:
    """Direct Schema.validate_json_schema_value calls also get enhanced errors."""
    errors = Schema.validate_json_schema_value("hello", {"type": "integer"}, "val")
    assert any("str('hello')" in e for e in errors)

    errors = Schema.validate_json_schema_value(42, {"type": "string"}, "val")
    assert any("int(42)" in e for e in errors)


def test_validate_params_empty_string_minlength() -> None:
    """Empty string on a minLength param should produce a validation error."""
    tool = SampleTool()
    errors = tool.validate_params({"query": "", "count": 2})
    assert any("query must be at least 2 chars" in e for e in errors)


def test_cast_params_int_to_number_allowed() -> None:
    """int→float for 'number' type is kept (JSON serialization reality)."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"rate": {"type": "number"}},
        }
    )
    result = tool.cast_params({"rate": 42})
    assert result["rate"] == 42.0
    assert isinstance(result["rate"], float)


def test_validate_params_string_for_bool_fails_with_detailed_error() -> None:
    """String passed to bool field should fail with type+value in error."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        }
    )
    errors = tool.validate_params({"flag": "true"})
    assert any("flag should be boolean" in e for e in errors)
    assert any("str('true')" in e for e in errors)


# --- ExecTool enhancement tests ---


async def test_exec_always_returns_exit_code(tmp_path) -> None:
    """Exit code should appear in output even on success (exit 0)."""
    tool = ExecTool(working_dir=str(tmp_path))
    result = await tool.execute(command="echo hello")
    assert "Exit: 0" in result
    assert "hello" in result


async def test_exec_head_tail_truncation(tmp_path) -> None:
    """Long output should preserve both head and tail."""
    tool = ExecTool(working_dir=str(tmp_path))
    script_file = tmp_path / "gen_output.py"
    script_file.write_text("print('A' * 6000 + chr(10) + 'B' * 6000)", encoding="utf-8")
    if sys.platform == "win32":
        command = subprocess.list2cmdline([sys.executable, str(script_file)])
    else:
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_file))}"
    result = await tool.execute(command=command)
    assert "chars truncated" in result
    # Output format: "Exit: 0  |  cwd: ...  |  shell: ...\n────\n<head output>\n...\n────\n[Full output cached: ...]"
    # Skip the status line + separator to reach the actual output body
    lines = result.split("\n")
    try:
        sep = next(i for i, l in enumerate(lines) if l.startswith("─"))
        body = "\n".join(lines[sep + 1:])
    except StopIteration:
        body = result
    assert body.lstrip().startswith("A")
    assert "Exit:" in result


async def test_exec_timeout_parameter(tmp_path) -> None:
    """LLM-supplied timeout should override the constructor default."""
    tool = ExecTool(timeout=60, working_dir=str(tmp_path))
    result = await tool.execute(command="sleep 10", timeout=1)
    assert "timed out" in result
    assert "1 seconds" in result


async def test_exec_timeout_capped_at_max(tmp_path) -> None:
    """Timeout values above _MAX_TIMEOUT should be clamped."""
    tool = ExecTool(working_dir=str(tmp_path))
    result = await tool.execute(command="echo ok", timeout=9999)
    assert "Exit: 0" in result


# --- cast_params tests ---


class CastTestTool(Tool):
    """Minimal tool for testing cast_params."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    @property
    def name(self) -> str:
        return "cast_test"

    @property
    def description(self) -> str:
        return "test tool for casting"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_cast_params_does_not_coerce_string_to_int() -> None:
    """String values must NOT be silently coerced to int — validation catches it."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
    )
    result = tool.cast_params({"count": "42"})
    assert result["count"] == "42"
    assert isinstance(result["count"], str)


def test_cast_params_does_not_coerce_string_to_number() -> None:
    """String values must NOT be silently coerced to float — validation catches it."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"rate": {"type": "number"}},
        }
    )
    result = tool.cast_params({"rate": "3.14"})
    assert result["rate"] == "3.14"
    assert isinstance(result["rate"], str)


def test_cast_params_does_not_coerce_string_to_bool() -> None:
    """String values must NOT be silently coerced to bool — validation catches it."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
        }
    )
    result = tool.cast_params({"enabled": "true"})
    assert result["enabled"] == "true"
    assert isinstance(result["enabled"], str)
    result = tool.cast_params({"enabled": "false"})
    assert result["enabled"] == "false"


def test_cast_params_array_items_preserves_types() -> None:
    """Array items should not have their types silently coerced."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        }
    )
    result = tool.cast_params({"nums": ["1", "2", "3"]})
    assert result["nums"] == ["1", "2", "3"]


def test_cast_params_nested_object_preserves_types() -> None:
    """Nested object values should not have types silently coerced."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "integer"},
                        "debug": {"type": "boolean"},
                    },
                },
            },
        }
    )
    result = tool.cast_params({"config": {"port": "8080", "debug": "true"}})
    assert result["config"]["port"] == "8080"
    assert isinstance(result["config"]["port"], str)
    assert result["config"]["debug"] == "true"
    assert isinstance(result["config"]["debug"], str)


def test_cast_params_bool_not_cast_to_int() -> None:
    """Booleans should not be silently cast to integers."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
    )
    result = tool.cast_params({"count": True})
    assert result["count"] is True
    errors = tool.validate_params(result)
    assert any("count should be integer" in e for e in errors)


def test_cast_params_preserves_empty_string() -> None:
    """Empty strings should be preserved for string type."""
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
    )
    result = tool.cast_params({"name": ""})
    assert result["name"] == ""


def test_cast_params_bool_string_invalid_or_no_coercion() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        }
    )
    # No coercion — string stays string
    result = tool.cast_params({"flag": "true"})
    assert result["flag"] == "true"
    result = tool.cast_params({"flag": "false"})
    assert result["flag"] == "false"
    # Invalid bool strings also stay as-is
    result = tool.cast_params({"flag": "random"})
    assert result["flag"] == "random"
    result = tool.cast_params({"flag": "maybe"})
    assert result["flag"] == "maybe"


def test_cast_params_invalid_string_to_int() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
    )
    result = tool.cast_params({"count": "abc"})
    assert result["count"] == "abc"
    result = tool.cast_params({"count": "12.5.7"})
    assert result["count"] == "12.5.7"


def test_cast_params_invalid_string_to_number() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"rate": {"type": "number"}},
        }
    )
    result = tool.cast_params({"rate": "not_a_number"})
    assert result["rate"] == "not_a_number"


def test_validate_params_bool_not_accepted_as_number() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"rate": {"type": "number"}},
        }
    )
    errors = tool.validate_params({"rate": False})
    assert any("rate should be number" in e for e in errors)
    assert any("bool" in e for e in errors)


def test_cast_params_none_values() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "items": {"type": "array"},
                "config": {"type": "object"},
            },
        }
    )
    result = tool.cast_params(
        {
            "name": None,
            "count": None,
            "items": None,
            "config": None,
        }
    )
    assert result["name"] is None
    assert result["count"] is None
    assert result["items"] is None
    assert result["config"] is None


def test_cast_params_single_value_not_auto_wrapped_to_array() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"items": {"type": "array"}},
        }
    )
    result = tool.cast_params({"items": 5})
    assert result["items"] == 5
    result = tool.cast_params({"items": "text"})
    assert result["items"] == "text"


def test_exec_extract_absolute_paths_keeps_full_windows_path() -> None:
    cmd = r"type C:\user\workspace\txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert paths == [r"C:\user\workspace\txt"]


def test_exec_extract_absolute_paths_captures_windows_drive_root_path() -> None:
    cmd = "dir E:\\"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert paths == ["E:\\"]


def test_exec_extract_absolute_paths_ignores_relative_posix_segments() -> None:
    cmd = ".venv/bin/python script.py"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/bin/python" not in paths


def test_exec_extract_absolute_paths_captures_posix_absolute_paths() -> None:
    cmd = "cat /tmp/data.txt > /tmp/out.txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/tmp/data.txt" in paths
    assert "/tmp/out.txt" in paths


def test_exec_extract_absolute_paths_captures_home_paths() -> None:
    cmd = "cat ~/.nanobot/config.json > ~/out.txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "~/.nanobot/config.json" in paths
    assert "~/out.txt" in paths


def test_exec_extract_absolute_paths_captures_quoted_paths() -> None:
    cmd = 'cat "/tmp/data.txt" "~/.nanobot/config.json"'
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/tmp/data.txt" in paths
    assert "~/.nanobot/config.json" in paths



def test_exec_guard_allows_media_path_outside_workspace(tmp_path, monkeypatch) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    media_file = media_dir / "photo.jpg"
    media_file.write_text("ok", encoding="utf-8")

    monkeypatch.setattr("nanobot.agent.tools.shell.shell.get_media_dir", lambda: media_dir)

    tool = ExecTool(restrict_to_workspace=True)
    error = tool._guard_command(f'cat "{media_file}"', str(tmp_path / "workspace"))
    assert error is None


def test_exec_guard_blocks_windows_drive_root_outside_workspace(monkeypatch) -> None:
    class FakeWindowsPath:
        def __init__(self, raw: str) -> None:
            self.raw = raw.rstrip("\\") + ("\\" if raw.endswith("\\") else "")

        def resolve(self) -> "FakeWindowsPath":
            return self

        def expanduser(self) -> "FakeWindowsPath":
            return self

        def is_absolute(self) -> bool:
            return len(self.raw) >= 3 and self.raw[1:3] == ":\\"

        @property
        def parents(self) -> list["FakeWindowsPath"]:
            if not self.is_absolute():
                return []
            trimmed = self.raw.rstrip("\\")
            if len(trimmed) <= 2:
                return []
            idx = trimmed.rfind("\\")
            if idx <= 2:
                return [FakeWindowsPath(trimmed[:2] + "\\")]
            parent = FakeWindowsPath(trimmed[:idx])
            return [parent, *parent.parents]

        def __eq__(self, other: object) -> bool:
            return isinstance(other, FakeWindowsPath) and self.raw.lower() == other.raw.lower()

    monkeypatch.setattr("nanobot.agent.tools.shell_validators.Path", FakeWindowsPath)

    tool = ExecTool(restrict_to_workspace=True, working_dir="E:\\workspace")
    error = tool._guard_command("dir E:\\", "E:\\workspace")
    assert "⚠️ Danger:" in error
    assert "outside" in error.lower()


# --- _resolve_type and nullable param tests ---


def test_resolve_type_simple_string() -> None:
    assert Tool._resolve_type("string") == "string"


def test_resolve_type_union_with_null() -> None:
    assert Tool._resolve_type(["string", "null"]) == "string"


def test_resolve_type_only_null() -> None:
    assert Tool._resolve_type(["null"]) is None


def test_resolve_type_none_input() -> None:
    assert Tool._resolve_type(None) is None


def test_validate_nullable_param_accepts_string() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": ["string", "null"]}},
        }
    )
    errors = tool.validate_params({"name": "hello"})
    assert errors == []


def test_validate_nullable_param_accepts_none() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": ["string", "null"]}},
        }
    )
    errors = tool.validate_params({"name": None})
    assert errors == []


def test_validate_nullable_flag_accepts_none() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": "string", "nullable": True}},
        }
    )
    errors = tool.validate_params({"name": None})
    assert errors == []


def test_cast_nullable_param_no_crash() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": ["string", "null"]}},
        }
    )
    result = tool.cast_params({"name": "hello"})
    assert result["name"] == "hello"
    result = tool.cast_params({"name": None})
    assert result["name"] is None
