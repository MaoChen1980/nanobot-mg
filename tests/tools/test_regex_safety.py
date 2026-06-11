"""Tests for ReDoS protection (_safe_regex_search) in tool modules.

Note: The ThreadPoolExecutor timeout cannot interrupt C-extension regex
backtracking because the GIL is not released by `re.search()`. The
_safe_regex_search function provides best-effort protection for patterns
where the GIL IS released (I/O-bound patterns, complex Unicode classes).
"""

from __future__ import annotations

import re

from nanobot.agent.tools.filesystem.filesystem_read import _safe_regex_search
from nanobot.agent.tools.web import _safe_regex_search as _safe_regex_search_web


class TestSafeRegexSearch:
    """_safe_regex_search correctly returns match/no-match."""

    def test_normal_regex_matches(self):
        assert _safe_regex_search(re.compile(r"hello"), "hello world") is True

    def test_normal_regex_no_match(self):
        assert _safe_regex_search(re.compile(r"xyz"), "hello world") is False

    def test_empty_pattern(self):
        assert _safe_regex_search(re.compile(r""), "anything") is True

    def test_unicode_pattern_match(self):
        assert _safe_regex_search(re.compile(r"你好"), "你好世界") is True

    def test_unicode_pattern_no_match(self):
        assert _safe_regex_search(re.compile(r"你好"), "hello") is False

    def test_dotall_flag(self):
        p = re.compile(r"hello.world", re.DOTALL)
        assert _safe_regex_search(p, "hello\nworld") is True

    def test_numeric_match(self):
        assert _safe_regex_search(re.compile(r"\d+"), "abc123def") is True

    def test_reusable_after_match(self):
        p = re.compile(r"\d+")
        for i in range(20):
            assert _safe_regex_search(p, f"test{i}data") is True


class TestSafeRegexSearchWeb:
    """Same function in web.py module."""

    def test_normal_match(self):
        assert _safe_regex_search_web(re.compile(r"hello"), "hello world") is True

    def test_no_match(self):
        assert _safe_regex_search_web(re.compile(r"xyz"), "hello world") is False

    def test_unicode(self):
        assert _safe_regex_search_web(re.compile(r"你好"), "你好世界") is True

    def test_reusable(self):
        p = re.compile(r"\w+")
        for i in range(10):
            assert _safe_regex_search_web(p, f"test{i}") is True
