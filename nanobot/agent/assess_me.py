"""Conversation self-assessment — read history, validate cognition state."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.utils.prompt_templates import render_template

_MAX_TOOL_RESULT_CHARS = 300
_ASSESSMENT_PREFIX = "No response needed, but a reminder:"


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
    provider: Any,
    model: str,
    verify: str = "",
) -> str | None:
    """Assess current cognition state from conversation history.

    Returns a structured analysis answering the 7 cognition questions,
    or *None* on failure.
    """
    conversation = format_conversation(messages)
    prompt = render_template("agent/assess_me.md", conversation=conversation, verify=verify)

    try:
        resp = await provider.chat_stream(
            [{"role": "user", "content": prompt}],
            model=model,
            max_tokens=1024,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning("assess_me LLM call failed: {}", e)
        return None

    return (resp.content or "").strip() or None


def build_assessment_message(text: str) -> dict[str, Any]:
    """Build a *user*-role message for injecting an assessment into history."""
    return {
        "role": "user",
        "content": f"{_ASSESSMENT_PREFIX}\n\n{text}",
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
