"""LLM 调用管道：处理 context window overflow 的透明重试。

MessagePipe 接收已组装好的 messages，发送给 LLM。如果 API 返回
context-window-exceeded 错误，自动按轮次压缩最旧部分 → 调 LLM 做 summary →
替换 → 重试。不关心 Session、budget、AgentLoop。

压缩结果通过 ``CompressEvent`` 返回给调用者，由调用者负责持久化。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from nanobot.agent.compressor import CompressEvent, Compressor
from nanobot.agent.llm_context import chat_stream_with_retry, chat_with_retry


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
        budget: int | None = None,
        previous_summary: str | None = None,
        **kwargs: Any,
    ) -> tuple[Any, CompressEvent | None]:
        """非流式调用，带 overflow 处理。

        Returns ``(response, compress_event)`` — 如果发生过压缩则
        *compress_event* 携带压缩结果，否则为 ``None``。
        """
        compress_event: CompressEvent | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_with_retry(messages=messages, **kwargs)
            if not _is_overflow(response):
                return response, compress_event
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages, event = await self._compress(messages, budget=budget, previous_summary=previous_summary)
            compress_event = event

        # Last attempt: send as-is (can't compress further)
        response = await chat_with_retry(messages=messages, **kwargs)
        return response, compress_event

    async def complete_stream(
        self,
        messages: list[dict],
        *,
        budget: int | None = None,
        previous_summary: str | None = None,
        on_content_delta: Any,
        on_reasoning_delta: Any,
        **kwargs: Any,
    ) -> tuple[Any, CompressEvent | None]:
        """流式调用，带 overflow 处理。

        Returns ``(response, compress_event)`` — 如果发生过压缩则
        *compress_event* 携带压缩结果，否则为 ``None``。
        """
        compress_event: CompressEvent | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_stream_with_retry(
                messages=messages,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                **kwargs,
            )
            if not _is_overflow(response):
                return response, compress_event
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages, event = await self._compress(messages, budget=budget, previous_summary=previous_summary)
            compress_event = event

        response = await chat_stream_with_retry(
            messages=messages,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            **kwargs,
        )
        return response, compress_event

    async def _compress(
        self,
        messages: list[dict],
        budget: int | None = None,
        previous_summary: str | None = None,
    ) -> tuple[list[dict], CompressEvent]:
        """渐进式压缩 messages 中最旧的轮次。

        Returns ``(compressed_messages, event)`` — *compressed_messages* 为
        压缩后的消息列表，*event* 携带替换掉的原始消息和 summary。
        """
        if len(messages) < 3:
            return messages, CompressEvent()

        history_msgs = messages[1:]
        all_turns = Compressor.split_turns(history_msgs)

        if len(all_turns) <= 1:
            if len(messages) >= 3:
                logger.warning(
                    "Single turn compression: dropping {} messages, keeping system + latest",
                    len(messages) - 2,
                )
                return [messages[0], messages[-1]], CompressEvent()
            return messages, CompressEvent()

        to_compress, keep = Compressor.split_by_budget(all_turns, budget=budget)

        if not to_compress:
            return messages, CompressEvent()

        logger.info(
            "CT_DBG: message_pipe compress {} of {} turns (budget={})",
            len(to_compress), len(all_turns), budget,
        )

        original_flat = [m for turn in to_compress for m in turn]
        event = await Compressor.compress(
            to_compress, keep,
            previous_summary=previous_summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Build result: system + synthetic pair + kept turns
        if event.synthetic_pair:
            result = [messages[0]] + event.synthetic_pair
        else:
            result = [messages[0]]
        for turn in keep:
            result.extend(turn)

        last_is_user = messages[-1].get("role") == "user"
        if last_is_user and (not result or result[-1].get("role") != "user"):
            result.append(messages[-1])

        event.replaced_raw = original_flat
        event.compressed_messages = result
        return result, event


def _is_overflow(response: Any) -> bool:
    """Check if the LLM response indicates a context window overflow."""
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason != "error":
        return False
    return _has_context_window_error(getattr(response, "content", None))
