"""Utility functions for AgentLoop."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from loguru import logger


if TYPE_CHECKING:
    pass


def strip_think(text: str | None) -> str | None:
    """Remove thinking blocks, unclosed trailing tags, and tokenizer-level
    template leaks occasionally emitted by some models (notably Gemma 4's
    Ollama renderer).

    Covers:
      1. Well-formed ``<think>...</think>`` and ``<thought>...</thought>`` blocks.
      2. Streaming prefixes where the block is never closed.
      3. *Malformed* opening tags missing the ``>`` -- e.g. ``<think广场…``.
      4. Harmony-style channel markers like ``<channel|>`` / ``<|channel|>``
         **at the start of the text** -- conservative to avoid eating
         explanatory prose that mentions these tokens.
      5. Orphan closing tags ``</think>`` / ``</thought>`` **at the very start
         or end of the text** only, for the same reason.
    """
    if not text:
        return text
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^\s*<think>[\s\S]*$", "", text)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text)
    text = re.sub(r"^\s*<thought>[\s\S]*$", "", text)
    text = re.sub(r"<think(?![A-Za-z0-9_\-:>/])", "", text)
    text = re.sub(r"<thought(?![A-Za-z0-9_\-:>/])", "", text)
    text = re.sub(r"^\s*</think>\s*", "", text)
    text = re.sub(r"\s*</think>\s*$", "", text)
    text = re.sub(r"^\s*</thought>\s*", "", text)
    text = re.sub(r"\s*</thought>\s*$", "", text)
    text = re.sub(r"^\s*<\|?channel\|?>\s*", "", text)
    # Strip framework-internal metadata tags (must not reach external channels)
    text = re.sub(r"\[assess\][\s\S]*?\[/assess\]", "", text)
    text = re.sub(r"\[debug_root_cause\][\s\S]*?\[/debug_root_cause\]", "", text)
    text = re.sub(r"\[tool_summary\][\s\S]*?\[/tool_summary\]", "", text)
    text = re.sub(r"\[/tool_summary\]", "", text)
    text = re.sub(r"<!--\s*no-assess\s*-->", "", text)
    text = re.sub(r"\[assess_me\]", "", text)
    text = re.sub(r"\(truncated,\s*\d+\s*chars?\)", "", text)
    text = re.sub(r"\[\.\.\.\d+\s+characters?\s+truncated\]", "", text)
    return text.strip()


def runtime_chat_id(msg: Any) -> str:
    """Return the chat id shown in runtime metadata for the model."""
    return str(msg.metadata.get("context_chat_id") or msg.chat_id)


def tool_hint(tool_calls: list) -> str:
    """Format tool calls as concise hints with smart abbreviation."""
    from nanobot.utils.tool_hints import format_tool_hints
    return format_tool_hints(tool_calls)


async def cancel_active_tasks(loop: Any, key: str) -> int:
    """Cancel and await all active tasks and subagents for *key*.

    Returns the total number of cancelled tasks + subagents.
    """
    state = loop._session_dispatch.pop(key, None)
    tasks = state.tasks if state else []
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected error during task cancellation in cancel_active_tasks")
    sub_cancelled = await loop.subagents.cancel_by_session(key)
    return cancelled + sub_cancelled
