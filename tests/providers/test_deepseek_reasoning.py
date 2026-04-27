"""Tests for DeepSeek reasoning mode in OpenAICompatProvider.

Covers:
- extra_body injection for thinking mode
- reasoning_content extraction from responses
- Multi-turn reasoning_content passthrough
- Drop of incomplete reasoning history
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nanobot.providers.openai_compat_provider import OpenAICompatProvider
from nanobot.providers.registry import ProviderSpec


_DEEPSEEK_SPEC = ProviderSpec(
    name="deepseek",
    keywords=("deepseek",),
    env_key="DEEPSEEK_API_KEY",
    display_name="DeepSeek",
    backend="openai_compat",
    default_api_base="https://api.deepseek.com",
    thinking_style="thinking_type",
)


# ── extra_body injection ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deepseek_injects_thinking_enabled_in_extra_body() -> None:
    """When reasoning_effort is set, extra_body contains thinking.type=enabled."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_response(content="42", reasoning_content="thinking...")

    with patch.object(provider._client.chat.completions, "create", mock_create):
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            model="deepseek-v4-pro",
            reasoning_effort="high",
        )

    assert captured_kwargs.get("reasoning_effort") == "high"
    assert captured_kwargs.get("extra_body") == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
async def test_deepseek_extra_body_absent_when_no_reasoning_effort() -> None:
    """When reasoning_effort is None, extra_body does not contain thinking."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_response(content="42")

    with patch.object(provider._client.chat.completions, "create", mock_create):
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            model="deepseek-v4-pro",
            reasoning_effort=None,
        )

    assert "extra_body" not in captured_kwargs or "thinking" not in captured_kwargs.get("extra_body", {})


@pytest.mark.asyncio
async def test_deepseek_injects_thinking_disabled_when_minimal_effort() -> None:
    """When reasoning_effort=minimal, thinking is disabled via extra_body."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_response(content="42")

    with patch.object(provider._client.chat.completions, "create", mock_create):
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            model="deepseek-v4-pro",
            reasoning_effort="minimal",
        )

    assert captured_kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}


# ── reasoning_content extraction ────────────────────────────────────────────────


def test_parse_extracts_reasoning_content_from_message() -> None:
    """reasoning_content field in response message is surfaced in LLMResponse."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    response = {
        "choices": [{
            "message": {
                "content": "The answer is 42.",
                "reasoning_content": "Let me calculate: 40 + 2 = 42",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }

    result = provider._parse(response)

    assert result.content == "The answer is 42."
    assert result.reasoning_content == "Let me calculate: 40 + 2 = 42"


def test_parse_extracts_reasoning_content_none_when_absent() -> None:
    """reasoning_content is None when response doesn't include it."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    response = {
        "choices": [{
            "message": {"content": "Hello!"},
            "finish_reason": "stop",
        }],
    }

    result = provider._parse(response)

    assert result.content == "Hello!"
    assert result.reasoning_content is None


# ── Multi-turn reasoning_content passthrough ───────────────────────────────────


@pytest.mark.asyncio
async def test_passthrough_preserves_reasoning_content_on_assistant_messages() -> None:
    """In multi-turn, reasoning_content on assistant messages is preserved."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    captured_messages = []

    async def mock_create(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        return _make_response(content="Done", reasoning_content="Final reasoning")

    with patch.object(provider._client.chat.completions, "create", mock_create):
        messages = [
            {"role": "user", "content": "What files are in memory?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}],
                "reasoning_content": "I need to list the files first",
            },
            {"role": "tool", "tool_call_id": "1", "name": "list_dir", "content": "MEMORY.md\nhistory.jsonl"},
        ]
        await provider.chat(
            messages,
            model="deepseek-v4-pro",
            reasoning_effort="high",
        )

    # The assistant message with tool_calls should preserve reasoning_content
    assistant_msgs = [m for m in captured_messages if m.get("role") == "assistant"]
    tool_call_msgs = [m for m in assistant_msgs if m.get("tool_calls")]
    assert len(tool_call_msgs) == 1
    assert tool_call_msgs[0].get("reasoning_content") == "I need to list the files first"


# ── _drop_deepseek_incomplete_reasoning_history ────────────────────────────────


def test_drop_patches_assistant_messages_with_tool_calls_but_no_reasoning() -> None:
    """Messages with tool_calls but missing reasoning_content are patched (not dropped)."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "list_dir", "arguments": "{}"}}],
            # Missing reasoning_content - patched to empty string
        },
        {"role": "tool", "tool_call_id": "1", "name": "list_dir", "content": "file1.txt"},
        {"role": "user", "content": "How many?"},
    ]

    result = provider._drop_deepseek_incomplete_reasoning_history(messages, "high")

    # All messages preserved, bad one is patched to have empty reasoning_content
    roles = [m.get("role") for m in result]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assistant_msgs = [m for m in result if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].get("reasoning_content") == ""


def test_drop_preserves_messages_with_reasoning_content() -> None:
    """Messages with both tool_calls and reasoning_content are preserved."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "list_dir", "arguments": "{}"}}],
            "reasoning_content": "I need to list the files to answer",
        },
        {"role": "tool", "tool_call_id": "1", "name": "list_dir", "content": "file1.txt"},
    ]

    result = provider._drop_deepseek_incomplete_reasoning_history(messages, "high")

    # All messages preserved since reasoning_content is present
    assert len(result) == len(messages)


def test_drop_does_nothing_when_reasoning_effort_is_none() -> None:
    """When reasoning_effort=None, history is not modified."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(spec=_DEEPSEEK_SPEC)

    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hi!", "tool_calls": []},
    ]

    result = provider._drop_deepseek_incomplete_reasoning_history(messages, None)

    assert result == messages


def test_drop_does_nothing_for_non_deepseek_providers() -> None:
    """Non-DeepSeek providers are not affected by the drop logic."""
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()  # No spec = generic OpenAI compat

    messages = [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}],
            # No reasoning_content - should NOT be dropped for non-DeepSeek
        },
        {"role": "tool", "tool_call_id": "1", "name": "x", "content": "ok"},
    ]

    result = provider._drop_deepseek_incomplete_reasoning_history(messages, "high")

    assert len(result) == len(messages)


# ── Helper ─────────────────────────────────────────────────────────────────────


def _make_response(content: str, reasoning_content: str | None = None) -> MagicMock:
    """Create a mock response with content and optional reasoning_content."""
    msg = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=None,
    )
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
