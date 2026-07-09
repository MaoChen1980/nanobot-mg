"""Tests for LLMProvider retry decision methods.

Covers: _is_transient_response, _is_retryable_429_response, _extract_retry_after,
_extract_retry_after_from_headers, _extract_retry_after_from_response.
All sync, zero mocking.
"""

from __future__ import annotations

import datetime

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse


class TestIsRetryable429Response:
    def test_non_retryable_error_tokens(self):
        for token in ("insufficient_quota", "quota_exceeded", "billing_hard_limit_reached"):
            resp = LLMResponse(content="", finish_reason="error", error_status_code=429, error_type=token)
            assert not LLMProvider._is_retryable_429_response(resp)

    def test_retryable_error_tokens(self):
        for token in ("rate_limit_exceeded", "too_many_requests", "overloaded_error"):
            resp = LLMResponse(content="", finish_reason="error", error_status_code=429, error_type=token)
            assert LLMProvider._is_retryable_429_response(resp)

    def test_non_retryable_text_markers(self):
        resp = LLMResponse(content="quota exceeded for today", finish_reason="error", error_status_code=429)
        assert not LLMProvider._is_retryable_429_response(resp)

    def test_retryable_text_markers(self):
        resp = LLMResponse(content="rate limit exceeded, retry later", finish_reason="error", error_status_code=429)
        assert LLMProvider._is_retryable_429_response(resp)

    def test_unknown_429_defaults_to_retryable(self):
        resp = LLMResponse(content="some unknown 429 error", finish_reason="error", error_status_code=429)
        assert LLMProvider._is_retryable_429_response(resp)

    def test_anthropic_quota_exhaustion_not_retryable(self):
        """Anthropic returns rate_limit_error for Token Plan quota exhaustion, but the
        content indicates upgrade/credits needed - not a temporary rate limit."""
        resp = LLMResponse(
            content="已达到 Token Plan 用量上限：请升级 Token Plan 套餐或购买积分补充用量",
            finish_reason="error",
            error_status_code=429,
            error_type="rate_limit_error",
        )
        assert not LLMProvider._is_retryable_429_response(resp)


class TestIsTransientResponse:
    def test_error_should_retry_true(self):
        resp = LLMResponse(content="", finish_reason="error", error_should_retry=True)
        assert LLMProvider._is_transient_response(resp)

    def test_error_should_retry_false(self):
        resp = LLMResponse(content="", finish_reason="error", error_should_retry=False)
        assert not LLMProvider._is_transient_response(resp)

    def test_status_code_429_delegates(self):
        resp = LLMResponse(content="rate limit", finish_reason="error", error_status_code=429, error_type="rate_limit")
        assert LLMProvider._is_transient_response(resp)

    def test_status_code_408(self):
        resp = LLMResponse(content="timeout", finish_reason="error", error_status_code=408)
        assert LLMProvider._is_transient_response(resp)

    def test_status_code_500(self):
        resp = LLMResponse(content="server error", finish_reason="error", error_status_code=500)
        assert LLMProvider._is_transient_response(resp)

    def test_timeout_kind(self):
        resp = LLMResponse(content="", finish_reason="error", error_kind="timeout")
        assert LLMProvider._is_transient_response(resp)

    def test_connection_kind(self):
        resp = LLMResponse(content="", finish_reason="error", error_kind="connection")
        assert LLMProvider._is_transient_response(resp)

    def test_fallback_to_text_marker(self):
        resp = LLMResponse(content="429 rate limit", finish_reason="error")
        assert LLMProvider._is_transient_response(resp)

    def test_non_transient(self):
        resp = LLMResponse(content="everything is fine", finish_reason="stop")
        assert not LLMProvider._is_transient_response(resp)


class TestExtractRetryAfter:
    def test_retry_after_seconds(self):
        result = LLMProvider._extract_retry_after("retry after 30 seconds")
        assert result == pytest.approx(30)

    def test_retry_after_minutes(self):
        result = LLMProvider._extract_retry_after("try again in 2 minutes")
        assert result == pytest.approx(120)

    def test_try_again_in(self):
        result = LLMProvider._extract_retry_after("try again in 5 seconds")
        assert result == pytest.approx(5)

    def test_wait_before_retry(self):
        result = LLMProvider._extract_retry_after("wait 10 seconds before retry")
        assert result == pytest.approx(10)

    def test_retry_after_json_style(self):
        result = LLMProvider._extract_retry_after('retry-after: 15')
        assert result == pytest.approx(15)

    def test_retry_after_quoted(self):
        result = LLMProvider._extract_retry_after('"retry_after": 20')
        assert result == pytest.approx(20)

    def test_retry_after_ms(self):
        result = LLMProvider._extract_retry_after("retry after 5000ms")
        assert result == pytest.approx(5)

    def test_no_match_returns_none(self):
        assert LLMProvider._extract_retry_after("everything is fine") is None

    def test_none_content(self):
        assert LLMProvider._extract_retry_after(None) is None


class TestExtractRetryAfterFromHeaders:
    def test_empty_headers(self):
        assert LLMProvider._extract_retry_after_from_headers(None) is None
        assert LLMProvider._extract_retry_after_from_headers({}) is None

    def test_retry_after_ms_header(self):
        headers = {"retry-after-ms": "5000"}
        result = LLMProvider._extract_retry_after_from_headers(headers)
        assert result == pytest.approx(5)

    def test_retry_after_seconds_header(self):
        headers = {"retry-after": "30"}
        result = LLMProvider._extract_retry_after_from_headers(headers)
        assert result == pytest.approx(30)

    def test_retry_after_http_date_header(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)
        http_date = future.strftime("%a, %d %b %Y %H:%M:%S %Z")
        headers = {"retry-after": http_date}
        result = LLMProvider._extract_retry_after_from_headers(headers)
        assert result is not None
        assert result >= 0.1

    def test_case_insensitive_header(self):
        headers = {"Retry-After": "15"}
        result = LLMProvider._extract_retry_after_from_headers(headers)
        assert result == pytest.approx(15)


class TestExtractRetryAfterFromResponse:
    def test_error_retry_after_s_preferred(self):
        resp = LLMResponse(content="", finish_reason="error",
                           error_retry_after_s=10, retry_after=5)
        assert LLMProvider._extract_retry_after_from_response(resp) == 10

    def test_retry_after_fallback(self):
        resp = LLMResponse(content="", finish_reason="error", retry_after=30)
        assert LLMProvider._extract_retry_after_from_response(resp) == 30

    def test_429_rate_limit_default(self):
        resp = LLMResponse(content="rate limit error", finish_reason="error",
                           error_status_code=429, error_type="rate_limit_error")
        assert LLMProvider._extract_retry_after_from_response(resp) == 120

    def test_429_rate_limit_in_content(self):
        resp = LLMResponse(content="rate_limit_error occurred", finish_reason="error",
                           error_status_code=429)
        assert LLMProvider._extract_retry_after_from_response(resp) == 120

    def test_fallback_to_text_extraction(self):
        resp = LLMResponse(content="retry after 15 seconds", finish_reason="error", error_status_code=500)
        assert LLMProvider._extract_retry_after_from_response(resp) == pytest.approx(15)

    def test_no_match_returns_none(self):
        resp = LLMResponse(content="ok", finish_reason="stop")
        assert LLMProvider._extract_retry_after_from_response(resp) is None
