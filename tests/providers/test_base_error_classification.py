"""Tests for LLMProvider error classification helpers.

Covers: _is_transient_error, _normalize_error_token, _extract_error_type_code, _to_retry_seconds.
All sync, zero mocking.
"""

from __future__ import annotations

import pytest

from nanobot.providers.base import LLMProvider


class TestIsTransientError:
    def test_429_marker(self):
        assert LLMProvider._is_transient_error("429 Too Many Requests")

    def test_rate_limit_marker(self):
        assert LLMProvider._is_transient_error("rate limit exceeded")

    def test_500_marker(self):
        assert LLMProvider._is_transient_error("500 Internal Server Error")

    def test_timeout_marker(self):
        assert LLMProvider._is_transient_error("request timed out")

    def test_connection_marker(self):
        assert LLMProvider._is_transient_error("connection error")

    def test_chinese_rate_limit(self):
        assert LLMProvider._is_transient_error("速率限制")

    def test_case_insensitive(self):
        assert LLMProvider._is_transient_error("RATE LIMIT Exceeded")

    def test_none_returns_false(self):
        assert not LLMProvider._is_transient_error(None)

    def test_unrelated_text_returns_false(self):
        assert not LLMProvider._is_transient_error("everything is fine")


class TestNormalizeErrorToken:
    def test_none_returns_none(self):
        assert LLMProvider._normalize_error_token(None) is None

    def test_whitespace_stripped(self):
        assert LLMProvider._normalize_error_token("  Rate_Limit  ") == "rate_limit"

    def test_empty_string_returns_none(self):
        assert LLMProvider._normalize_error_token("   ") is None

    def test_normalizes_case(self):
        assert LLMProvider._normalize_error_token("InsufficientQuota") == "insufficientquota"


class TestExtractErrorTypeCode:
    def test_dict_payload(self):
        payload = {"error": {"type": "rate_limit", "code": "too_many_requests"}}
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype == "rate_limit"
        assert ecode == "too_many_requests"

    def test_string_json_payload(self):
        payload = '{"error": {"type": "insufficient_quota", "code": "quota_exceeded"}}'
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype == "insufficient_quota"
        assert ecode == "quota_exceeded"

    def test_non_json_string_returns_none_none(self):
        payload = "this is not json"
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype is None
        assert ecode is None

    def test_top_level_type_code(self):
        payload = {"type": "rate_limit", "code": "too_many_requests"}
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype == "rate_limit"
        assert ecode == "too_many_requests"

    def test_error_object_overrides_top_level(self):
        payload = {"type": "old", "error": {"type": "new", "code": "new"}}
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype == "new"
        assert ecode == "new"

    def test_non_dict_payload(self):
        assert LLMProvider._extract_error_type_code(123) == (None, None)

    def test_none_payload(self):
        assert LLMProvider._extract_error_type_code(None) == (None, None)

    def test_empty_string(self):
        assert LLMProvider._extract_error_type_code("") == (None, None)

    def test_dict_without_error_fields(self):
        payload = {"message": "something happened"}
        etype, ecode = LLMProvider._extract_error_type_code(payload)
        assert etype is None
        assert ecode is None


class TestToRetrySeconds:
    def test_value_in_seconds(self):
        assert LLMProvider._to_retry_seconds(30, "s") == 30

    def test_value_in_milliseconds(self):
        assert LLMProvider._to_retry_seconds(5000, "ms") == pytest.approx(5.0)

    def test_value_in_minutes(self):
        assert LLMProvider._to_retry_seconds(2, "m") == 120

    def test_clamped_to_minimum(self):
        assert LLMProvider._to_retry_seconds(0, "s") == 0.1
        assert LLMProvider._to_retry_seconds(-5, "s") == 0.1

    def test_empty_unit_defaults_to_seconds(self):
        assert LLMProvider._to_retry_seconds(10) == 10

    def test_various_unit_spellings(self):
        assert LLMProvider._to_retry_seconds(1, "minutes") == 60
        assert LLMProvider._to_retry_seconds(1, "min") == 60
        assert LLMProvider._to_retry_seconds(1000, "milliseconds") == 1.0
        assert LLMProvider._to_retry_seconds(1, "sec") == 1
        assert LLMProvider._to_retry_seconds(1, "secs") == 1
