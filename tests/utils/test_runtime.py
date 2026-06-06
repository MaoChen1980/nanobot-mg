"""Tests for nanobot.utils.runtime — runtime helper functions."""

from nanobot.utils.runtime import (
    build_finalization_retry_message,
    build_length_recovery_message,
    empty_tool_result_message,
    ensure_nonempty_tool_result,
    external_lookup_signature,
    is_blank_text,
    check_repeated_external_lookup,
)


class TestEmptyToolResultMessage:
    def test_returns_formatted_string(self):
        assert empty_tool_result_message("read_file_tool") == "(read_file_tool completed with no output)"


class TestEnsureNonemptyToolResult:
    def test_none_content(self):
        assert ensure_nonempty_tool_result("foo", None) == "(foo completed with no output)"

    def test_empty_string(self):
        assert ensure_nonempty_tool_result("bar", "") == "(bar completed with no output)"

    def test_blank_string(self):
        assert ensure_nonempty_tool_result("baz", "  ") == "(baz completed with no output)"

    def test_nonempty_string_unchanged(self):
        assert ensure_nonempty_tool_result("t", "hello") == "hello"

    def test_empty_list(self):
        assert ensure_nonempty_tool_result("x", []) == "(x completed with no output)"

    def test_list_with_blank_text_blocks(self):
        content = [{"type": "text", "text": "  "}]
        assert ensure_nonempty_tool_result("y", content) == "(y completed with no output)"

    def test_list_with_content(self):
        content = [{"type": "text", "text": "result"}]
        assert ensure_nonempty_tool_result("z", content) == content

    def test_non_string_non_list_unchanged(self):
        assert ensure_nonempty_tool_result("n", 42) == 42


class TestIsBlankText:
    def test_none(self):
        assert is_blank_text(None) is True

    def test_empty(self):
        assert is_blank_text("") is True

    def test_whitespace(self):
        assert is_blank_text("  \n\t") is True

    def test_non_empty(self):
        assert is_blank_text("hello") is False


class TestBuildMessages:
    def test_finalization_retry(self):
        msg = build_finalization_retry_message()
        assert msg["role"] == "user"
        assert "response to the user" in msg["content"].lower()

    def test_length_recovery(self):
        msg = build_length_recovery_message()
        assert msg["role"] == "user"
        assert "output limit" in msg["content"].lower()


class TestExternalLookupSignature:
    def test_web_fetch_with_url(self):
        sig = external_lookup_signature("web_fetch_tool", {"url": "HTTPS://Example.COM/doc"})
        assert sig == "web_fetch:https://example.com/doc"

    def test_web_fetch_empty_url(self):
        assert external_lookup_signature("web_fetch_tool", {"url": ""}) is None

    def test_web_fetch_missing_url(self):
        assert external_lookup_signature("web_fetch_tool", {}) is None

    def test_web_search_with_query(self):
        sig = external_lookup_signature("web_search_tool", {"query": "Python 3.13"})
        assert sig == "web_search:python 3.13"

    def test_web_search_with_search_term(self):
        sig = external_lookup_signature("web_search_tool", {"search_term": "async"})
        assert sig == "web_search:async"

    def test_web_search_empty_query(self):
        assert external_lookup_signature("web_search_tool", {"query": ""}) is None

    def test_other_tool_returns_none(self):
        assert external_lookup_signature("read_file_tool", {"path": "/tmp"}) is None


class TestRepeatedExternalLookupError:
    def test_first_call_returns_none(self):
        seen = {}
        result = check_repeated_external_lookup("web_fetch_tool", {"url": "http://example.com"}, seen)
        assert result is None
        assert seen == {"web_fetch:http://example.com": 1}

    def test_second_call_returns_none(self):
        seen = {"web_search:python": 1}
        result = check_repeated_external_lookup("web_search_tool", {"query": "Python"}, seen)
        assert result is None
        assert seen["web_search:python"] == 2

    def test_third_call_blocked(self):
        seen = {"web_fetch:http://example.com": 2}
        result = check_repeated_external_lookup("web_fetch_tool", {"url": "http://example.com"}, seen)
        assert result is not None
        assert "blocked" in result
        assert seen["web_fetch:http://example.com"] == 3

    def test_non_tracked_tool_returns_none(self):
        seen = {}
        result = check_repeated_external_lookup("read_file_tool", {"path": "/tmp"}, seen)
        assert result is None
        assert seen == {}
