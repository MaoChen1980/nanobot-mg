"""Tests for nanobot.providers.factory — make_provider and provider_signature."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nanobot.config.schema import Config
from nanobot.providers.factory import (
    build_provider_snapshot,
    make_provider,
    provider_signature,
)


# ---------------------------------------------------------------------------
# make_provider — Azure OpenAI validation
# ---------------------------------------------------------------------------


def test_azure_openai_missing_api_base_raises():
    """Azure OpenAI with api_key but no api_base raises ValueError."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "azure/gpt-4"}},
        "providers": {"azureOpenai": {"apiKey": "test-key"}},
    })
    with pytest.raises(ValueError, match="Azure OpenAI requires"):
        make_provider(config)


@patch("nanobot.providers.azure_openai_provider.AzureOpenAIProvider")
def test_azure_openai_backend(mock_provider_cls):
    """Azure OpenAI with valid api_key and api_base creates AzureOpenAIProvider."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "azure/gpt-4"}},
        "providers": {"azureOpenai": {"apiKey": "test-key", "apiBase": "https://test.openai.azure.com"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once()
    args, kwargs = mock_provider_cls.call_args
    assert kwargs["api_key"] == "test-key"
    assert kwargs["api_base"] == "https://test.openai.azure.com"
    assert kwargs["default_model"] == "azure/gpt-4"
    assert provider is not None


# ---------------------------------------------------------------------------
# make_provider — missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_for_non_exempt_provider_raises():
    """Non-exempt, non-local provider without API key raises."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "deepseek/deepseek-chat"}},
    })
    with pytest.raises(ValueError, match="No API key configured"):
        make_provider(config)


# ---------------------------------------------------------------------------
# make_provider — backend selection
# ---------------------------------------------------------------------------


@patch("nanobot.providers.openai_codex_provider.OpenAICodexProvider")
def test_openai_codex_backend(mock_provider_cls):
    """openai_codex backend instantiates OpenAICodexProvider."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "openai-codex/gpt-5.1-codex"}},
        "providers": {"openaiCodex": {"apiKey": "test-key"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once_with(default_model="openai-codex/gpt-5.1-codex")
    assert provider is not None


@patch("nanobot.providers.anthropic_provider.AnthropicProvider")
def test_anthropic_backend(mock_provider_cls):
    """anthropic backend instantiates AnthropicProvider."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "anthropic/claude-opus-4-5"}},
        "providers": {"anthropic": {"apiKey": "sk-ant-test"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once()
    args, kwargs = mock_provider_cls.call_args
    assert kwargs["api_key"] == "sk-ant-test"
    assert kwargs["default_model"] == "anthropic/claude-opus-4-5"
    assert provider is not None


@patch("nanobot.providers.anthropic_provider.AnthropicProvider")
def test_anthropic_backend_with_extra_headers(mock_provider_cls):
    """anthropic backend passes extra_headers when configured."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "anthropic/claude-opus-4-5"}},
        "providers": {
            "anthropic": {
                "apiKey": "sk-ant-test",
                "extraHeaders": {"X-Custom": "value"},
            }
        },
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once()
    args, kwargs = mock_provider_cls.call_args
    assert kwargs["extra_headers"] == {"X-Custom": "value"}
    assert provider is not None


@patch("nanobot.providers.github_copilot_provider.GitHubCopilotProvider")
def test_github_copilot_backend(mock_provider_cls):
    """github_copilot backend instantiates GitHubCopilotProvider."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "github-copilot/gpt-5.3-codex"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once_with(default_model="github-copilot/gpt-5.3-codex")
    assert provider is not None


# ---------------------------------------------------------------------------
# make_provider — default openai_compat backend
# ---------------------------------------------------------------------------


@patch("nanobot.providers.openai_compat_provider.OpenAICompatProvider")
def test_openai_compat_backend(mock_provider_cls):
    """Default backend uses OpenAICompatProvider (bedrock/ prefix skips key check)."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "bedrock/test-model"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once()
    args, kwargs = mock_provider_cls.call_args
    assert kwargs["api_key"] is None
    assert kwargs["default_model"] == "bedrock/test-model"
    assert kwargs["spec"] is None
    assert provider is not None


@patch("nanobot.providers.openai_compat_provider.OpenAICompatProvider")
def test_openai_compat_backend_with_key(mock_provider_cls):
    """OpenAI compat provider receives the matched provider's api_key."""
    config = Config.model_validate({
        "agents": {"defaults": {"model": "deepseek/deepseek-chat"}},
        "providers": {"deepseek": {"apiKey": "sk-ds-test"}},
    })
    provider = make_provider(config)

    mock_provider_cls.assert_called_once()
    args, kwargs = mock_provider_cls.call_args
    assert kwargs["api_key"] == "sk-ds-test"
    assert kwargs["default_model"] == "deepseek/deepseek-chat"
    assert provider is not None


# ---------------------------------------------------------------------------
# provider_signature
# ---------------------------------------------------------------------------


def test_provider_signature_basic():
    """provider_signature returns a tuple of config fields affecting the LLM."""
    config = Config.model_validate({
        "agents": {
            "defaults": {
                "model": "anthropic/claude-opus-4-5",
                "maxTokens": 4096,
                "temperature": 0.3,
                "reasoningEffort": "high",
                "contextWindowTokens": 100_000,
            },
        },
        "providers": {"anthropic": {"apiKey": "sk-ant-test"}},
    })
    sig = provider_signature(config)

    assert sig[0] == "anthropic/claude-opus-4-5"  # model
    assert sig[1] == "auto"  # defaults.provider
    assert sig[2] == "anthropic"  # get_provider_name
    assert sig[3] == "sk-ant-test"  # get_api_key
    assert sig[5] == 4096  # max_tokens
    assert sig[6] == 0.3  # temperature
    assert sig[7] == "high"  # reasoning_effort
    assert sig[8] == 100_000  # context_window_tokens



# ---------------------------------------------------------------------------
# build_provider_snapshot / load_provider_snapshot
# ---------------------------------------------------------------------------


@patch("nanobot.providers.factory.build_provider_snapshot")
def test_load_provider_snapshot(mock_build, tmp_path):
    """load_provider_snapshot loads config, resolves env vars, and builds."""
    from nanobot.providers.factory import load_provider_snapshot

    config_path = tmp_path / "config.json"
    config_path.write_text('{"agents": {"defaults": {"model": "test/model"}}}', encoding="utf-8")

    mock_build.return_value = "fake_snapshot"
    result = load_provider_snapshot(config_path)

    mock_build.assert_called_once()
    call_config = mock_build.call_args[0][0]
    assert call_config.agents.defaults.model == "test/model"
    assert result == "fake_snapshot"
