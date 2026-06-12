"""Constants and callback helpers for AgentRunner."""

from __future__ import annotations

_DEFAULT_ERROR_MESSAGE = "抱歉，调用 AI 模型时出现错误。"
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[模型异常，助手回复不可用。]"
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_MAX_INJECTIONS_PER_TURN = 50
_MAX_INJECTION_CYCLES = 20
_MAX_MODEL_ERROR_RETRIES = 1  # Number of times to let LLM retry after content-safety errors
_SNIP_SAFETY_BUFFER = 4096
_BACKFILL_CONTENT = "[工具结果不可用 — 调用被中断或结果丢失]"

