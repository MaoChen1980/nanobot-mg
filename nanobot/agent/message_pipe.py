"""LLM 调用管道：处理 context window overflow 的透明重试。

MessagePipe 接收已组装好的 messages，发送给 LLM。如果 API 返回
context-window-exceeded 错误，自动按轮次压缩最旧部分 → 调 LLM 做 summary →
替换 → 重试。不关心 Session、budget、AgentLoop。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from nanobot.agent.llm_context import chat_stream_with_retry, chat_with_retry
from nanobot.session.manager import Session


_HAS_CONTEXT_WINDOW_MARKERS = (
    "context window",
    "maximum context",
    "prompt is too long",
    "too many tokens",
    "token limit",
    "context length",
)


def _has_context_window_error(content: str | None) -> bool:
    """Check if an LLM error response indicates the context window was exceeded."""
    if not content:
        return False
    lowered = content.lower()
    return any(m in lowered for m in _HAS_CONTEXT_WINDOW_MARKERS)


class MessagePipe:
    """LLM 调用管道。

    职责：
    - 发送 messages 给 provider.chat()
    - 检测 overflow → 按轮次压缩 → 调 LLM 做 summary → 替换 → 重试
    - 绝不操作 Session、不关心 channel/chat_id
    """

    MAX_RETRIES = 3

    async def complete(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> Any:
        """非流式调用，带 overflow 处理。"""
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_with_retry(messages=messages, **kwargs)
            if not _is_overflow(response):
                return response
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages = await self._compress(messages)

        # Last attempt: send as-is (can't compress further)
        return await chat_with_retry(messages=messages, **kwargs)

    async def complete_stream(
        self,
        messages: list[dict],
        *,
        on_content_delta: Any,
        on_reasoning_delta: Any,
        **kwargs: Any,
    ) -> Any:
        """流式调用，带 overflow 处理。"""
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_stream_with_retry(
                messages=messages,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                **kwargs,
            )
            if not _is_overflow(response):
                return response
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages = await self._compress(messages)

        return await chat_stream_with_retry(
            messages=messages,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            **kwargs,
        )

    async def _compress(self, messages: list[dict]) -> list[dict]:
        """压缩 messages 中最旧的轮次 — 异步（调 LLM 做 summary）。"""
        from nanobot.agent.loop_utils import strip_think
        from nanobot.agent.compress import summarize_turns

        if len(messages) < 3:
            return messages

        # messages[0] = system prompt, rest = history + current user message
        history_msgs = messages[1:]
        turns = Session._split_turns_by_assistant(history_msgs)

        if len(turns) <= 1:
            # Single turn — can't compress by turns. Fallback: drop
            # everything except system + newest user message.
            if len(messages) >= 3:
                logger.warning(
                    "Single turn compression: dropping {} messages, keeping system + latest",
                    len(messages) - 2,
                )
                return [messages[0], messages[-1]]
            return messages

        # Keep the newest turn, compress everything older
        keep = turns[-1:]
        to_compress = turns[:-1]

        compress_flat = [m for turn in to_compress for m in turn]
        future_context = [m for turn in keep for m in turn]

        summary = await summarize_turns(
            compress_flat,
            future_context=future_context,
        )
        summary = strip_think(summary).strip() if summary else ""

        last_is_user = messages[-1].get("role") == "user"

        if summary:
            ts = datetime.now(timezone.utc).isoformat()
            synthetic_pair = [
                {"role": "assistant", "content": summary, "timestamp": ts, "status": "synthetic"},
                {"role": "user", "content": "ok", "timestamp": ts, "status": "synthetic"},
            ]
            result: list[dict] = [messages[0]] + synthetic_pair
        else:
            result = [messages[0]]

        for turn in keep:
            result.extend(turn)

        if last_is_user and (not result or result[-1].get("role") != "user"):
            result.append(messages[-1])

        return result


def _is_overflow(response: Any) -> bool:
    """Check if the LLM response indicates a context window overflow."""
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason != "error":
        return False
    return _has_context_window_error(getattr(response, "content", None))
