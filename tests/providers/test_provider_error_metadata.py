from types import SimpleNamespace
from unittest.mock import MagicMock

from anthropic import APIStatusError

from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.openai_compat_provider import OpenAICompatProvider


def _fake_response(
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        text=text,
    )


def test_openai_handle_error_extracts_structured_metadata() -> None:
    class FakeStatusError(Exception):
        pass

    err = FakeStatusError("boom")
    err.status_code = 409
    err.response = _fake_response(
        status_code=409,
        headers={"retry-after-ms": "250", "x-should-retry": "false"},
        text='{"error":{"type":"rate_limit_exceeded","code":"rate_limit_exceeded"}}',
    )
    err.body = {"error": {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}}

    response = OpenAICompatProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 409
    assert response.error_type == "rate_limit_exceeded"
    assert response.error_code == "rate_limit_exceeded"
    assert response.error_retry_after_s == 0.25
    assert response.error_should_retry is False


def test_openai_handle_error_marks_timeout_kind() -> None:
    class FakeTimeoutError(Exception):
        pass

    response = OpenAICompatProvider._handle_error(FakeTimeoutError("timeout"))

    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"


def test_openai_handle_error_marks_connection_kind_from_message() -> None:
    """error_kind is inferred from exception message when class name is generic."""

    class FakeGenericError(Exception):
        pass

    response = OpenAICompatProvider._handle_error(
        FakeGenericError("Connection error.")
    )

    assert response.finish_reason == "error"
    assert response.error_kind == "connection"
    # Connection errors are always retried (transient network/server issue).
    assert response.error_should_retry is True


def test_anthropic_handle_error_extracts_structured_metadata() -> None:
    err = MagicMock(spec=APIStatusError)
    err.status_code = 408
    err.body = {"type": "error", "error": {"type": "rate_limit_error"}}
    err.response = MagicMock(headers={"retry-after": "1.5", "x-should-retry": "true"})

    response = AnthropicProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 408
    assert response.error_type == "rate_limit_error"
    assert response.error_retry_after_s == 1.5
    assert response.error_should_retry is True


def test_anthropic_handle_error_marks_connection_kind() -> None:
    class FakeConnectionError(Exception):
        pass

    response = AnthropicProvider._handle_error(FakeConnectionError("connection"))

    assert response.finish_reason == "error"
    assert response.error_kind == "connection"
    assert response.error_should_retry is True
    assert response.error_retry_after_s == 30.0


def test_openai_handle_error_5xx_overload_error_is_transient() -> None:
    """HTTP 529 overloaded_error from OpenAI-compat provider is transient — should retry."""

    class FakeServerError(Exception):
        pass

    err = FakeServerError("overloaded")
    err.status_code = 529
    err.response = SimpleNamespace(
        status_code=529,
        headers={},
        text='{"type": "overloaded_error", "message": "当前服务集群负载较高"}',
    )
    err.body = {"type": "overloaded_error", "message": "当前服务集群负载较高"}

    response = OpenAICompatProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 529
    assert response.error_type == "overloaded_error"
    # 5xx should trigger retry
    assert response.error_should_retry is True
    # Conservative 30 s back-off when no Retry-After header
    assert response.error_retry_after_s == 30.0


def test_openai_handle_error_500_internal_error_is_transient() -> None:
    """HTTP 500 from OpenAI-compat provider is transient — should retry."""

    class FakeServerError(Exception):
        pass

    err = FakeServerError("internal server error")
    err.status_code = 500
    err.response = SimpleNamespace(status_code=500, headers={}, text="Internal Server Error")
    err.body = {"error": {"type": "internal_server_error"}}

    response = OpenAICompatProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 500
    assert response.error_should_retry is True
    assert response.error_retry_after_s == 30.0
