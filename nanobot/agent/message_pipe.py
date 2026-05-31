"""LLM 调用管道：处理 context window overflow 的透明重试。

MessagePipe 接收已组装好的 messages，发送给 LLM。如果 API 返回
context-window-exceeded 错误，自动按轮次压缩最旧部分 → 调 LLM 做 summary →
替换 → 重试。不关心 Session、budget、AgentLoop。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

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
        model: str,
        provider: Any,
        **kwargs: Any,
    ) -> Any:
        """非流式调用，带 overflow 处理。"""
        for attempt in range(self.MAX_RETRIES + 1):
            response = await provider.chat_with_retry(
                messages=messages, model=model, **kwargs
            )
            if not _is_overflow(response):
                return response
            logger.warning(
                "Overflow detected (attempt {}/{}), compressing...",
                attempt + 1, self.MAX_RETRIES,
            )
            messages = await self._compress(messages, provider, model)

        # Last attempt: send as-is (can't compress further)
        return await provider.chat_with_retry(messages=messages, model=model, **kwargs)

    async def complete_stream(
        self,
        messages: list[dict],
        model: str,
        provider: Any,
        *,
        on_content_delta: Any,
        on_reasoning_delta: Any,
        **kwargs: Any,
    ) -> Any:
        """流式调用，带 overflow 处理。"""
        for attempt in range(self.MAX_RETRIES + 1):
            response = await provider.chat_stream_with_retry(
                messages=messages, model=model,
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
            messages = await self._compress(messages, provider, model)

        return await provider.chat_stream_with_retry(
            messages=messages, model=model,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            **kwargs,
        )

    async def _compress(self, messages: list[dict], provider: Any, model: str) -> list[dict]:
        """压缩 messages 中最旧的轮次 — 异步（调 LLM 做 summary）。"""
        from nanobot.agent.loop_utils import strip_think

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

        summary = await self._summarize_turns(
            compress_flat, future_context, provider, model, strip_think,
        )

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

    async def _summarize_turns(
        self,
        turns: list[dict],
        future_context: list[dict] | None,
        provider: Any,
        model: str,
        strip_think: Any,
    ) -> str:
        """Summarize oldest turns via LLM, guided by future context.

        Retries on network errors; reduces content and retries on overflow.
        """
        def _format_turns(msgs: list[dict]) -> str:
            lines: list[str] = []
            for msg in msgs:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if isinstance(content, str):
                    lines.append(f"<{role}>\n{content}\n</{role}>")
                elif isinstance(content, list):
                    texts = [
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    joined = "\n".join(texts)
                    if joined:
                        lines.append(f"<{role}>\n{joined}\n</{role}>")
            return "\n".join(lines)

        def _build_prompt(msgs: list[dict], future: list[dict] | None) -> str:
            turns_text = _format_turns(msgs)
            future_text = _format_turns(future) if future else ""
            return (
                "你正在总结即将被裁剪的旧对话 turns。\n"
                "任务：你**后面**的对话（附在后面）是当前正在进行的上下文。\n"
                "请根据后面的对话来判断：前面的对话中，哪些信息对后面的对话仍然重要？\n"
                "\n"
                "## 一些可参考的方向（由你判断是否适用）\n"
                "- 后面还在引用的现实细节，比如： 地址、坐标、金额、文件路径、参数名、配置值、API 签名、接口约定、消息格式\n"
                "- 后面还在依赖的架构决策、选择理由\n"
                "- 后续步骤依赖的前置条件、状态、配置\n"
                "- 明确的选型决定（我们决定用 X 不选 Y，因为……）\n"
                "- 排除掉的替代方案及其排除原因\n"
                "- 达成共识的方案、配置、参数值\n"
                "- 后面还在讨论的话题、瓶颈、下一步计划\n"
                "- 当前任务的目标和进度\n"
                "- 已尝试但未完成的下一步\n"
                "- 未解决的阻塞点\n"
                "- 踩过的坑和解决方案\n"
                "- 已验证不可行的路径及原因\n"
                "\n"
                "## 一些可以考虑丢弃的方向（由你判断）\n"
                "- 后面的对话已不再使用的试错过程\n"
                "- 已被后续值替代的旧值\n"
                "- 寒暄\n"
                "\n"
                "## 最重要的原则\n"
                "以上方向仅供参考。你的判断比这些建议更重要。\n"
                "如果你觉得某个信息在后面还有用，不管它属于哪类，都保留。\n"
                "如果你觉得某个信息后面已经用不上了，不管它看似多重要，都丢弃。\n"
                "\n"
                "关键原则：同一类信息，只保留最晚的那个版本。\n"
                "## 输出\n"
                "简洁的要点列表，按主题分组。不要按 turn 顺序。\n"
                "\n"
                "以下是即将被裁剪的旧对话：\n\n"
                f"{turns_text}\n\n"
                "---\n"
                "以下是后面（会保留的）对话，请参考它们来判断上面的旧对话中哪些信息仍然重要：\n\n"
                f"{future_text}"
            )

        current_turns = turns
        current_future = future_context

        for attempt in range(6):
            prompt = _build_prompt(current_turns, current_future)

            try:
                resp = await provider.chat(
                    [{"role": "user", "content": prompt}],
                    model=model,
                )
            except Exception as e:
                logger.warning("Summary attempt {}/6 failed (network): {}", attempt + 1, e)
                if attempt < 5:
                    await asyncio.sleep(10)
                    continue
                return ""

            # Overflow — reduce content and retry
            if _is_overflow(resp):
                logger.warning("Summary overflow on attempt {}/6, reducing content", attempt + 1)
                if attempt < 5 and len(current_turns) > 1:
                    mid = len(current_turns) // 2
                    current_turns = current_turns[mid:]
                    if current_future and len(current_future) > 1:
                        mid_future = len(current_future) // 2
                        current_future = current_future[mid_future:]
                    continue
                # Single turn — truncate content rather than giving up
                if attempt < 5 and len(current_turns) == 1:
                    msg = current_turns[0]
                    content = msg.get("content", "")
                    if isinstance(content, str) and len(content) > 200:
                        half = len(content) // 2
                        # Create new dict to avoid mutating originals
                        current_turns = [{"role": msg["role"], "content": content[:half] + "\n...(truncated)"}]
                        # Also truncate future_context if it's a single large message
                        if current_future and len(current_future) == 1:
                            fc_msg = current_future[0]
                            fc_content = fc_msg.get("content", "")
                            if isinstance(fc_content, str) and len(fc_content) > 200:
                                fc_half = len(fc_content) // 2
                                current_future = [{"role": fc_msg["role"], "content": fc_content[:fc_half] + "\n...(truncated)"}]
                            else:
                                current_future = []
                        else:
                            current_future = []
                        continue
                return ""

            # Success
            summary = strip_think(resp.content or "").strip()
            logger.info("Summarized {} turns ({} chars)", len(current_turns), len(summary))
            return summary or "(no context to preserve)"

        return ""


def _is_overflow(response: Any) -> bool:
    """Check if the LLM response indicates a context window overflow."""
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason != "error":
        return False
    return _has_context_window_error(getattr(response, "content", None))
