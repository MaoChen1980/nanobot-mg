"""Conversation self-assessment — read history, validate cognition state."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.llm_context import chat_stream_with_retry
from nanobot.utils.prompt_templates import render_template

_MAX_TOOL_RESULT_CHARS = 300
_ASSESSMENT_PREFIX = "[assess]"
_ASSESSMENT_SUFFIX = "\n[/assess]"


def format_conversation(messages: list[dict]) -> str:
    """Format message list as readable conversation text for the assessment LLM.

    Skips system prompt. Truncates long tool results. Collapses tool-call-only
    assistant messages into a single line.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            continue

        if role == "tool":
            name = msg.get("name", "?")
            if isinstance(content, str) and content:
                if len(content) > _MAX_TOOL_RESULT_CHARS:
                    content = content[:_MAX_TOOL_RESULT_CHARS] + \
                        f"... (truncated, {len(content)} chars)"
                parts.append(f"[tool:{name}] {content}")
            continue

        if role == "assistant" and not content:
            tc = msg.get("tool_calls")
            if tc:
                names = [
                    c.get("function", {}).get("name", c.get("name", "?"))
                    for c in tc[:5]
                ]
                parts.append(f"[assistant → calls: {', '.join(names)}]")
            continue

        if isinstance(content, list):
            texts: list[str] = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
            content = "\n".join(texts)

        if isinstance(content, str) and len(content) > _MAX_TOOL_RESULT_CHARS:
            content = content[:_MAX_TOOL_RESULT_CHARS] + \
                f"... (truncated, {len(content)} chars)"

        if content:
            parts.append(f"[{role}] {content}")

    return "\n\n".join(parts)


async def assess_me(
    messages: list[dict[str, Any]],
    verify: str = "",
    tree_data: str = "",
) -> str:
    """Assess current cognition state from conversation history.

    Returns a structured analysis answering the 7 cognition questions.
    Never returns ``None``. Returns ``""`` when the LLM call fails —
    callers handle empty assessments according to their context.
    """
    conversation = format_conversation(messages)
    prompt = render_template("agent/assess_me.md", conversation=conversation, verify=verify, tree=tree_data)

    resp = await chat_stream_with_retry(
        [{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    if resp.finish_reason == "error":
        logger.warning("assess_me LLM call failed: {}", (resp.content or "")[:200])
        return ""
    if not resp.content:
        logger.warning(
            "assess_me LLM returned empty content (finish_reason={})",
            resp.finish_reason,
        )
        return ""
    return resp.content.strip()


def build_assessment_message(text: str) -> dict[str, Any]:
    """Build a *user*-role message for injecting an assessment into history."""
    return {
        "role": "user",
        "content": (
            f"{_ASSESSMENT_PREFIX}\n{text.strip()}{_ASSESSMENT_SUFFIX}\n\n"
            "---\n以上为上下文评估，请据此继续推进原始任务，无需回应此消息。"
        ),
    }


def is_assessment_message(msg: dict) -> bool:
    """Check if a message is an assessment (prefixed with the reminder marker).

    Only matches ``role=user`` messages — tool results with the same prefix
    (from explicit ``assess_me_tool`` calls) are left alone.
    """
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    return isinstance(content, str) and content.startswith(_ASSESSMENT_PREFIX)


_DEBUG_RC_PREFIX = "[debug_root_cause]"
_DEBUG_RC_SUFFIX = "\n[/debug_root_cause]"


def build_debug_root_cause_message(text: str) -> dict[str, Any]:
    """Build a *user*-role message for injecting a debug_root_cause into history."""
    return {
        "role": "user",
        "content": (
            f"{_DEBUG_RC_PREFIX}\n{text.strip()}{_DEBUG_RC_SUFFIX}\n\n"
            "---\n以上为根因分析，请结合分析继续推进任务，无需回应此消息。"
        ),
    }


def is_debug_root_cause_message(msg: dict) -> bool:
    """Check if a message is a debug_root_cause injection."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    return isinstance(content, str) and content.startswith(_DEBUG_RC_PREFIX)
