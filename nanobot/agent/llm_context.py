"""Unified LLM call interface — tools and modules call chat()/chat_stream_with_retry()
without knowing the provider or model.

Both ``AgentLoop`` and ``SubagentManager`` call ``set_llm()`` at startup to inject
the active provider+model into this module's ContextVars.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider, LLMResponse

_llm_provider: ContextVar["LLMProvider"] = ContextVar("llm_provider")
_llm_model: ContextVar[str] = ContextVar("llm_model")


def set_llm(provider: "LLMProvider", model: str) -> None:
    """Inject the active provider + default model at startup."""
    _llm_provider.set(provider)
    _llm_model.set(model)


async def chat(
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> "LLMResponse":
    """Call the provider's ``chat_stream()`` — no retry, simple LLM call.

    Accepts any keyword argument that ``LLMProvider.chat_stream`` supports
    (model, max_tokens, temperature, tools, tool_choice, etc.).
    If ``model`` is not passed, the default model from ``set_llm()`` is used.
    """
    provider = _llm_provider.get()
    if "model" not in kwargs:
        kwargs["model"] = _llm_model.get()
    return await provider.chat_stream(messages=messages, **kwargs)


async def chat_stream_with_retry(
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> "LLMResponse":
    """Call the provider's ``chat_stream_with_retry()`` — transient errors retried.

    Accepts any keyword argument that ``LLMProvider.chat_stream_with_retry``
    supports.
    """
    provider = _llm_provider.get()
    if "model" not in kwargs:
        kwargs["model"] = _llm_model.get()
    return await provider.chat_stream_with_retry(messages=messages, **kwargs)


async def chat_with_retry(
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> "LLMResponse":
    """Call the provider's ``chat_with_retry()`` — non-streaming, with retry."""
    provider = _llm_provider.get()
    if "model" not in kwargs:
        kwargs["model"] = _llm_model.get()
    return await provider.chat_with_retry(messages=messages, **kwargs)
