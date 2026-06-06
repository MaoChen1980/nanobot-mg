"""Tests for LLMProvider sanitization methods (_sanitize_empty_content, etc.)."""

from __future__ import annotations

from nanobot.providers.base import LLMProvider


def test_sanitize_empty_content_empty_string_becomes_empty():
    """Empty string content for non-assistant becomes '(empty)'."""
    messages = [{"role": "user", "content": ""}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert result[0]["content"] == "(empty)"


def test_sanitize_empty_content_empty_string_with_tool_calls_becomes_none():
    """Empty string content for assistant with tool_calls becomes None."""
    messages = [{"role": "assistant", "content": "", "tool_calls": [{"id": "tc_1"}]}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert result[0]["content"] is None


def test_sanitize_empty_content_empty_text_blocks_removed():
    """Empty text/input_text/output_text blocks are removed from list content."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": ""},
        {"type": "text", "text": "hello"},
        {"type": "input_text", "text": ""},
        {"type": "output_text", "text": ""},
    ]}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert len(result[0]["content"]) == 1
    assert result[0]["content"][0]["text"] == "hello"


def test_sanitize_empty_content_strips_meta_keys():
    """_meta keys are stripped from dict items in list content."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "hello", "_meta": {"path": "/tmp/x.png"}},
        {"type": "image_url", "image_url": {"url": "data:image/png,..."}, "_meta": {"path": "/tmp/x.png"}},
    ]}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert "_meta" not in result[0]["content"][0]
    assert "_meta" not in result[0]["content"][1]


def test_sanitize_empty_content_dict_content_wrapped_in_list():
    """Dict content gets wrapped in a list."""
    messages = [{"role": "user", "content": {"type": "text", "text": "hello"}}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert isinstance(result[0]["content"], list)
    assert result[0]["content"][0]["text"] == "hello"


def test_sanitize_empty_content_non_empty_string_passthrough():
    """Non-empty string content passes through unchanged."""
    messages = [{"role": "user", "content": "hello"}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert result[0]["content"] == "hello"


def test_sanitize_empty_content_empty_list_all_removed_becomes_none():
    """When all items are removed from list and assistant has tool_calls, content becomes None."""
    messages = [{"role": "assistant", "content": [{"type": "text", "text": ""}], "tool_calls": [{"id": "tc_1"}]}]
    result = LLMProvider._sanitize_empty_content(messages)
    assert result[0]["content"] is None


def test_sanitize_request_messages_only_allowed_keys():
    """Only keys in allowed_keys frozenset are preserved."""
    messages = [{"role": "user", "content": "hi", "extra": "drop", "_meta": "drop"}]
    result = LLMProvider._sanitize_request_messages(messages, frozenset({"role", "content"}))
    assert list(result[0].keys()) == ["role", "content"]


def test_sanitize_request_messages_assistant_missing_content():
    """Assistant message without content key gets content=None."""
    messages = [{"role": "assistant", "tool_calls": [{"id": "tc_1"}]}]
    result = LLMProvider._sanitize_request_messages(messages, frozenset({"role", "content", "tool_calls"}))
    assert result[0]["content"] is None


def test_replace_image_content_replaces_image_url():
    """image_url blocks are replaced with text placeholder."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "data:image/png,..."}, "_meta": {"path": "/tmp/x.png"}},
    ]}]
    result = LLMProvider._replace_image_content(messages)
    assert result is not None
    assert result[0]["content"][1]["type"] == "text"


def test_replace_image_content_no_images_returns_none():
    """When no images found, returns None."""
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    result = LLMProvider._replace_image_content(messages)
    assert result is None


def test_replace_image_content_inplace_mutates():
    """_replace_image_content_inplace mutates the original list and returns True."""
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png,..."}, "_meta": {"path": "/tmp/x.png"}},
    ]}]
    result = LLMProvider._replace_image_content_inplace(messages)
    assert result is True
    assert messages[0]["content"][0]["type"] == "text"


def test_replace_image_content_inplace_no_images_returns_false():
    """When no images found, inplace returns False and does not mutate."""
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    result = LLMProvider._replace_image_content_inplace(messages)
    assert result is False
