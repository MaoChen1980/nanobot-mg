"""Tests for nanobot.utils.tool_hints — tool call formatting and abbreviation."""

from nanobot.utils.tool_hints import (
    _abbreviate_command,
    _extract_arg,
    _fmt_fallback,
    _fmt_known,
    _fmt_mcp,
    _get_args,
    format_tool_hints,
)


class _FakeTC:
    """Minimal tool-call-like object with name + arguments."""
    def __init__(self, name: str, arguments=None):
        self.name = name
        self.arguments = arguments or {}


# ---------------------------------------------------------------------------
# _get_args
# ---------------------------------------------------------------------------


class TestGetArgs:
    def test_none_arguments(self):
        assert _get_args(_FakeTC("t", None)) == {}

    def test_dict_arguments(self):
        assert _get_args(_FakeTC("t", {"a": 1})) == {"a": 1}

    def test_list_arguments_uses_first(self):
        assert _get_args(_FakeTC("t", [{"a": 1}, {"b": 2}])) == {"a": 1}

    def test_empty_list_arguments(self):
        assert _get_args(_FakeTC("t", [])) == {}

    def test_non_dict_non_list_returns_empty(self):
        assert _get_args(_FakeTC("t", "string")) == {}


# ---------------------------------------------------------------------------
# _extract_arg
# ---------------------------------------------------------------------------


class TestExtractArg:
    def test_returns_first_matching_key(self):
        tc = _FakeTC("t", {"path": "/tmp/x", "pattern": "*.py"})
        assert _extract_arg(tc, ["path", "file_path"]) == "/tmp/x"

    def test_returns_second_key_when_first_missing(self):
        tc = _FakeTC("t", {"file_path": "/tmp/x"})
        assert _extract_arg(tc, ["path", "file_path"]) == "/tmp/x"

    def test_falls_back_to_first_string_value(self):
        tc = _FakeTC("t", {"query": "hello"})
        assert _extract_arg(tc, ["path"]) == "hello"

    def test_returns_none_when_no_string_values(self):
        tc = _FakeTC("t", {"count": 42})
        assert _extract_arg(tc, ["path"]) is None

    def test_non_dict_args_returns_none(self):
        assert _extract_arg(_FakeTC("t", None), ["path"]) is None

    def test_args_is_non_dict_returns_none(self):
        """_get_args returning non-dict (list first element isn't dict) hits the type guard."""
        tc = _FakeTC("t", [42])  # list, first element is int, not a dict
        assert _extract_arg(tc, ["path"]) is None


# ---------------------------------------------------------------------------
# _fmt_known
# ---------------------------------------------------------------------------


class TestFmtKnown:
    def test_read_file_abbreviates_path(self):
        tc = _FakeTC("read_file", {"path": "/very/long/path/that/exceeds/the/default/abbreviation/limit/file.py"})
        result = _fmt_known(tc, (["path", "file_path"], "read {}", True, False))
        assert result.startswith("read ")
        assert "…" in result

    def test_exec_formats_command(self):
        tc = _FakeTC("exec", {"command": "ls -la /tmp"})
        result = _fmt_known(tc, (["command"], "$ {}", False, True))
        assert result == "$ ls -la /tmp"

    def test_no_arg_falls_back_to_tool_name(self):
        tc = _FakeTC("read_file", {})
        result = _fmt_known(tc, (["path", "file_path"], "read {}", True, False))
        assert result == "read_file"


# ---------------------------------------------------------------------------
# _abbreviate_command
# ---------------------------------------------------------------------------


class TestAbbreviateCommand:
    def test_short_command_unchanged(self):
        assert _abbreviate_command("ls -la", 40) == "ls -la"

    def test_abbreviates_paths_in_command(self):
        cmd = _abbreviate_command("cat /home/user/very/long/path/file.txt", 40)
        assert "file.txt" in cmd
        assert "…" in cmd

    def test_truncates_long_abbreviated_command(self):
        cmd = _abbreviate_command("echo" + " x" * 50, 30)
        assert len(cmd) == 30
        assert cmd.endswith("…")

    def test_quoted_paths_abbreviated(self):
        cmd = _abbreviate_command('cat "/home/user/very long/path/file.txt"', 40)
        assert "file.txt" in cmd
        assert "…" in cmd

    def test_single_quoted_path_abbreviated(self):
        """Single-quoted paths are also matched by _PATH_IN_CMD_RE."""
        cmd = _abbreviate_command("cat '/home/user/very/long/path/file.txt'", 40)
        assert "file.txt" in cmd
        assert "…" in cmd


# ---------------------------------------------------------------------------
# _fmt_mcp
# ---------------------------------------------------------------------------


class TestFmtMcp:
    def test_server_and_tool_with_args(self):
        tc = _FakeTC("mcp_filesystem__read", {"path": "/some/file.txt"})
        result = _fmt_mcp(tc)
        assert result.startswith("filesystem::read(")

    def test_server_and_tool_without_args(self):
        tc = _FakeTC("mcp_filesystem__list", {})
        result = _fmt_mcp(tc)
        assert result == "filesystem::list"

    def test_single_segment_name(self):
        tc = _FakeTC("mcp_tool", {"arg": "val"})
        result = _fmt_mcp(tc)
        assert "mcp_tool" in result

    def test_no_tool_returns_name(self):
        tc = _FakeTC("mcp_", {})
        result = _fmt_mcp(tc)
        assert result == "mcp_"


# ---------------------------------------------------------------------------
# _fmt_fallback
# ---------------------------------------------------------------------------


class TestFmtFallback:
    def test_returns_name_when_no_args(self):
        tc = _FakeTC("custom_tool", {})
        assert _fmt_fallback(tc) == "custom_tool"

    def test_short_arg_wraps_in_quotes(self):
        tc = _FakeTC("custom_tool", {"input": "hello"})
        assert _fmt_fallback(tc) == 'custom_tool("hello")'

    def test_long_arg_abbreviates(self):
        tc = _FakeTC("custom_tool", {"input": "/this/is/a/very/long/path/that/should/be/abbreviated/file.txt"})
        result = _fmt_fallback(tc)
        assert result.startswith('custom_tool("')
        assert "…" in result

    def test_non_string_arg_returns_name(self):
        tc = _FakeTC("custom_tool", {"count": 42})
        assert _fmt_fallback(tc) == "custom_tool"


# ---------------------------------------------------------------------------
# format_tool_hints — integration
# ---------------------------------------------------------------------------


class TestFormatToolHints:
    def test_empty_tool_calls(self):
        assert format_tool_hints([]) == ""

    def test_known_read_file(self):
        tc = _FakeTC("read_file", {"path": "/tmp/test.py"})
        result = format_tool_hints([tc])
        assert result.startswith("read ")
        assert "test.py" in result

    def test_known_exec(self):
        tc = _FakeTC("exec", {"command": "npm test"})
        result = format_tool_hints([tc])
        assert result == "$ npm test"

    def test_mcp_tool(self):
        tc = _FakeTC("mcp_filesystem__write", {"path": "/tmp/foo.txt"})
        result = format_tool_hints([tc])
        assert "filesystem::write" in result

    def test_fallback_tool(self):
        tc = _FakeTC("unknown_tool", {"arg": "val"})
        result = format_tool_hints([tc])
        assert result == 'unknown_tool("val")'

    def test_deduplicates_repeated_same_tool(self):
        tc = _FakeTC("read_file", {"path": "/tmp/a.py"})
        result = format_tool_hints([tc, tc])
        assert "× 2" in result

    def test_mixed_tool_types(self):
        hints = format_tool_hints([
            _FakeTC("exec", {"command": "git status"}),
            _FakeTC("read_file", {"path": "/tmp/main.py"}),
        ])
        assert "$ git status" in hints
        assert "read " in hints
        assert "main.py" in hints
