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
        budget: int | None = None,
        **kwargs: Any,
    ) -> tuple[Any, list[dict] | None]:
        """非流式调用，带 overflow 处理。

        Returns ``(response, compressed_messages)`` — 如果发生过压缩则
        *compressed_messages* 为压缩后的消息列表，否则为 ``None``。
        """
        compressed_messages: list[dict] | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_with_retry(messages=messages, **kwargs)
            if not _is_overflow(response):
                return response, compressed_messages
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages = await self._compress(messages, budget=budget)
            compressed_messages = messages

        # Last attempt: send as-is (can't compress further)
        response = await chat_with_retry(messages=messages, **kwargs)
        return response, compressed_messages

    async def complete_stream(
        self,
        messages: list[dict],
        *,
        budget: int | None = None,
        on_content_delta: Any,
        on_reasoning_delta: Any,
        **kwargs: Any,
    ) -> tuple[Any, list[dict] | None]:
        """流式调用，带 overflow 处理。

        Returns ``(response, compressed_messages)`` — 如果发生过压缩则
        *compressed_messages* 为压缩后的消息列表，否则为 ``None``。
        """
        compressed_messages: list[dict] | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            response = await chat_stream_with_retry(
                messages=messages,
                on_content_delta=on_content_delta,
                on_reasoning_delta=on_reasoning_delta,
                **kwargs,
            )
            if not _is_overflow(response):
                return response, compressed_messages
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages = await self._compress(messages, budget=budget)
            compressed_messages = messages

        response = await chat_stream_with_retry(
            messages=messages,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            **kwargs,
        )
        return response, compressed_messages

    async def _compress(self, messages: list[dict], budget: int | None = None) -> list[dict]:
        """渐进式压缩 messages 中最旧的轮次。

        1. 按 budget 确定保留轮次
        2. 50 轮一批，从最旧开始渐进压缩
        3. 每批用后续 10 轮作 relevance 判断
        4. 批间通过 previous_summary 合并

        无 budget 时保持向后兼容：只保留最新 1 轮，其余全压。
        """
        from nanobot.agent.compress import (
            COMPRESS_BATCH_SIZE, FUTURE_TURNS, _take_future_turns,
            compress_turns,
        )
        from nanobot.utils.helpers import estimate_message_tokens

        if len(messages) < 3:
            return messages

        history_msgs = messages[1:]
        turns = Session._split_turns_by_assistant(history_msgs)

        if len(turns) <= 1:
            if len(messages) >= 3:
                logger.warning(
                    "Single turn compression: dropping {} messages, keeping system + latest",
                    len(messages) - 2,
                )
                return [messages[0], messages[-1]]
            return messages

        if budget is not None:
            keep_start = len(turns)
            used = 0
            for i in range(len(turns) - 1, -1, -1):
                turn_tokens = sum(estimate_message_tokens(m) for m in turns[i])
                if keep_start < len(turns) and used + turn_tokens > budget:
                    break
                used += turn_tokens
                keep_start = i
            keep_start = max(0, min(keep_start, len(turns) - 1))
        else:
            keep_start = max(0, len(turns) - 1)

        keep = turns[keep_start:]
        to_compress = turns[:keep_start]

        if not to_compress:
            return messages

        logger.info(
            "Compressing {} of {} turns (budget={}, keeping {} turns)",
            len(to_compress), len(turns), budget, len(keep),
        )

        prev_summary = None
        synthetic_pair: list[dict] = []
        for batch_start in range(0, len(to_compress), COMPRESS_BATCH_SIZE):
            chunk = to_compress[batch_start:batch_start + COMPRESS_BATCH_SIZE]
            chunk_flat = [m for turn in chunk for m in turn]
            future_context = _take_future_turns(
                to_compress, batch_start, len(chunk),
                FUTURE_TURNS, keep,
            )

            summary, pair = await compress_turns(
                chunk_flat, future_context,
                previous_summary=prev_summary,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            if not pair:
                if prev_summary is None:
                    logger.warning("First compression batch failed, keeping raw turns")
                    result: list[dict] = [messages[0]]
                    for turn in keep:
                        result.extend(turn)
                    return result
                break
            prev_summary = summary
            synthetic_pair = pair

        last_is_user = messages[-1].get("role") == "user"

        if synthetic_pair:
            result = [messages[0]] + synthetic_pair
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
