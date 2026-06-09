"""Anthropic provider — direct SDK integration for Claude models."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import string
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import json_repair
from loguru import logger

from anthropic import APIStatusError
from anthropic.lib.streaming._messages import (
    ParsedMessageStopEvent,
    TextEvent,
    ThinkingEvent,
)
from anthropic.types import (
    RedactedThinkingBlock,
    RefusalStopDetails,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers._tool_call_parser import (
    detect_unparsed_tool_calls,
    extract_xml_tool_calls,
)

_ALNUM = string.ascii_letters + string.digits


def _gen_tool_id() -> str:
    return "toolu_" + "".join(secrets.choice(_ALNUM) for _ in range(22))


class AnthropicProvider(LLMProvider):
    """LLM provider using the native Anthropic SDK for Claude models.

    Handles message format conversion (OpenAI → Anthropic Messages API),
    prompt caching, extended thinking, tool calls, and streaming.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "claude-sonnet-4-20250514",
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}

        from anthropic import AsyncAnthropic

        client_kw: dict[str, Any] = {}
        if api_key:
            client_kw["api_key"] = api_key
        if api_base:
            client_kw["base_url"] = api_base
        if extra_headers:
            client_kw["default_headers"] = extra_headers
        # Keep retries centralized in LLMProvider._run_with_retry to avoid retry amplification.
        client_kw["max_retries"] = 0
        self._client = AsyncAnthropic(**client_kw)

    @classmethod
    def _handle_error(cls, e: Exception) -> LLMResponse:
        # Primary path: SDK APIStatusError with structured fields
        if isinstance(e, APIStatusError):
            status_code = e.status_code
            body = e.body
            headers = e.response.headers if e.response else None

            payload_text = ""
            if isinstance(body, dict):
                error_obj = body.get("error", {})
                if isinstance(error_obj, dict):
                    payload_text = error_obj.get("message", "") or ""
                if not payload_text:
                    payload_text = body.get("message") or ""
            elif isinstance(body, str):
                payload_text = body
            msg = (
                f"Error: {payload_text.strip()[:500]}"
                if payload_text.strip()
                else f"Error calling LLM: {e}"
            )

            retry_after = cls._extract_retry_after_from_headers(headers)
            if retry_after is None:
                retry_after = LLMProvider._extract_retry_after(msg)

            should_retry: bool | None = None
            if headers is not None:
                raw = headers.get("x-should-retry")
                if isinstance(raw, str):
                    lowered = raw.strip().lower()
                    if lowered == "true":
                        should_retry = True
                    elif lowered == "false":
                        should_retry = False

            error_kind = LLMProvider._classify_error(e)
            error_type, error_code = LLMProvider._extract_error_type_code(body)

            logger.exception(
                "Anthropic API error: status={}, type={}, code={}, msg={}",
                status_code, error_type, error_code, msg[:200],
            )

            return LLMResponse(
                content=msg,
                finish_reason="error",
                retry_after=retry_after,
                error_status_code=status_code,
                error_kind=error_kind,
                error_type=error_type,
                error_code=error_code,
                error_retry_after_s=retry_after,
                error_should_retry=should_retry,
            )

        # Fallback for non-APIStatusError exceptions (connection, timeout, etc.)
        logger.exception("Anthropic API error (non-APIStatusError): {}", e)
        return LLMResponse(
            content=f"Error calling LLM: {e}",
            finish_reason="error",
            error_kind=LLMProvider._classify_error(e),
        )

    @staticmethod
    def _dump_prompt_on_error(
        messages: list[dict[str, Any]],
        error_status: int,
        error_msg: str,
        model: str,
    ) -> str | None:
        """Dump the full prompt to a file on 5xx errors for offline analysis.

        Returns the dump file path, or None if skipped/failed.
        """
        if error_status < 500:
            return None
        dump_dir = os.environ.get(
            "NANOBOT_DUMP_DIR",
            os.path.expanduser("~/.nanobot/dumps"),
        )
        os.makedirs(dump_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"prompt_dump_{ts}_http{error_status}.json"
        filepath = os.path.join(dump_dir, filename)
        try:
            payload = {
                "error": {"status": error_status, "message": error_msg},
                "model": model,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "messages": messages,
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            logger.warning("Prompt dumped to {} ({} messages)", filepath, len(messages))
            return filepath
        except Exception as e:
            logger.warning("Failed to dump prompt: {}", e)
            return None

    @staticmethod
    def _strip_prefix(model: str) -> str:
        if model.startswith("anthropic/"):
            return model[len("anthropic/"):]
        return model

    # ------------------------------------------------------------------
    # Message conversion: OpenAI chat format → Anthropic Messages API
    # ------------------------------------------------------------------

    def _convert_messages(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(system, anthropic_messages)``."""
        system: str | list[dict[str, Any]] = ""
        raw: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "system":
                text = content if isinstance(content, str) else str(content or "")
                system = (system + "\n\n" + text) if system else text
                continue

            if role == "tool":
                block = self._tool_result_block(msg)
                if raw and raw[-1]["role"] == "user":
                    prev_c = raw[-1]["content"]
                    if isinstance(prev_c, list):
                        raw[-1] = {**raw[-1], "content": list(prev_c) + [block]}
                    else:
                        raw[-1] = {**raw[-1], "content": [
                            {"type": "text", "text": prev_c or ""}, block,
                        ]}
                else:
                    raw.append({"role": "user", "content": [block]})
                continue

            if role == "assistant":
                raw.append({"role": "assistant", "content": self._assistant_blocks(msg)})
                continue

            if role == "user":
                raw.append({
                    "role": "user",
                    "content": self._convert_user_content(content),
                })
                continue

        return system, self._merge_consecutive(raw)

    @staticmethod
    def _tool_result_block(msg: dict[str, Any]) -> dict[str, Any]:
        content = msg.get("content")
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": msg.get("tool_call_id", ""),
        }
        if isinstance(content, list):
            block["content"] = AnthropicProvider._convert_user_content(content)
        elif isinstance(content, str):
            block["content"] = content
        else:
            block["content"] = str(content) if content else ""
        return block

    @staticmethod
    def _assistant_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        content = msg.get("content")

        for tb in msg.get("thinking_blocks") or []:
            if isinstance(tb, dict) and tb.get("type") == "thinking":
                blocks.append({
                    "type": "thinking",
                    "thinking": tb.get("thinking", ""),
                    "signature": tb.get("signature", ""),
                })

        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for item in content:
                blocks.append(item if isinstance(item, dict) else {"type": "text", "text": str(item)})

        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                args = json_repair.loads(args)
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or _gen_tool_id(),
                "name": func.get("name", ""),
                "input": args,
            })

        return blocks or [{"type": "text", "text": ""}]

    @staticmethod
    def _convert_user_content(content: Any) -> Any:
        """Convert user message content, translating image_url blocks."""
        if isinstance(content, str) or content is None:
            return content or "(empty)"
        if not isinstance(content, list):
            return str(content)

        result: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                result.append({"type": "text", "text": str(item)})
                continue
            if item.get("type") == "image_url":
                converted = AnthropicProvider._convert_image_block(item)
                if converted:
                    result.append(converted)
                continue
            result.append(item)
        return result or "(empty)"

    @staticmethod
    def _convert_image_block(block: dict[str, Any]) -> dict[str, Any] | None:
        """Convert OpenAI image_url block to Anthropic image block."""
        url = (block.get("image_url") or {}).get("url", "")
        if not url:
            return None
        m = re.match(r"data:(image/\w+);base64,(.+)", url, re.DOTALL)
        if m:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
            }
        return {
            "type": "image",
            "source": {"type": "url", "url": url},
        }

    @staticmethod
    def _has_tool_use(msg: dict[str, Any]) -> bool:
        """True if ``msg.content`` carries any ``tool_use`` block.

        Anthropic forbids ``tool_use`` inside ``user`` turns, so messages that
        issued a tool call cannot be safely rerouted when we patch the role.
        """
        content = msg.get("content")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )

    @staticmethod
    def _merge_consecutive(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize a message sequence for Anthropic's ``/messages`` endpoint.

        Anthropic's contract is stricter than OpenAI's:

        1. Consecutive same-role turns must be collapsed into one.
        2. The conversation cannot end with an ``assistant`` turn — Anthropic
           does not support assistant-message prefill and returns 400.
        3. The conversation cannot start with an ``assistant`` turn — the
           first message must be ``user``.

        Rules 2 and 3 mirror ``LLMProvider._enforce_role_alternation`` in
        ``base.py``, which applies the equivalent invariants to OpenAI-compat
        providers.  The only Anthropic-specific wrinkle: ``tool_use`` blocks
        live inside ``content`` (not a separate ``tool_calls`` field) and are
        invalid inside ``user`` turns, so the recovery paths below must skip
        any message carrying them rather than silently producing a malformed
        request.
        """
        merged: list[dict[str, Any]] = []
        for msg in msgs:
            if merged and merged[-1]["role"] == msg["role"]:
                prev_c = merged[-1]["content"]
                cur_c = msg["content"]
                if isinstance(prev_c, str):
                    prev_c = [{"type": "text", "text": prev_c}]
                if isinstance(cur_c, str):
                    cur_c = [{"type": "text", "text": cur_c}]
                if isinstance(cur_c, list):
                    prev_c.extend(cur_c)
                merged[-1]["content"] = prev_c
            else:
                merged.append(msg)

        # Rule 2: strip trailing assistant turns — Anthropic rejects prefill.
        last_popped: dict[str, Any] | None = None
        while merged and merged[-1].get("role") == "assistant":
            last_popped = merged.pop()

        # Recovery for rule 2: if stripping removed every turn, reroute the
        # last popped assistant as a user turn so upstream code still gets a
        # valid request instead of a secondary "messages array empty" 400.
        # Skip when the message carried ``tool_use`` blocks (see _has_tool_use).
        if (
            not merged
            and last_popped is not None
            and not AnthropicProvider._has_tool_use(last_popped)
        ):
            merged.append({"role": "user", "content": last_popped.get("content")})

        # Rule 3: prepend a synthetic opener if the first surviving turn is an
        # assistant (e.g. upstream history truncation dropped the original
        # user request).  ``tool_use``-carrying assistants are left alone —
        # that message will still fail validation, but injecting an opener
        # before it would orphan the tool_use/tool_result pair that follows,
        # turning a recoverable 400 into a harder-to-diagnose one.
        if (
            merged
            and merged[0].get("role") == "assistant"
            and not AnthropicProvider._has_tool_use(merged[0])
        ):
            merged.insert(0, {"role": "user", "content": "(conversation continued)"})

        return merged

    # ------------------------------------------------------------------
    # Tool definition conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        result = []
        for tool in tools:
            func = tool.get("function", tool)
            entry: dict[str, Any] = {
                "name": func.get("name", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
            desc = func.get("description")
            if desc:
                entry["description"] = desc
            if "cache_control" in tool:
                entry["cache_control"] = tool["cache_control"]
            result.append(entry)
        return result

    @staticmethod
    def _convert_tool_choice(
        tool_choice: str | dict[str, Any] | None,
        thinking_enabled: bool = False,
    ) -> dict[str, Any] | None:
        if thinking_enabled:
            return {"type": "auto"}
        if tool_choice is None or tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "none":
            return None
        if isinstance(tool_choice, dict):
            name = tool_choice.get("function", {}).get("name")
            if name:
                return {"type": "tool", "name": name}
        return {"type": "auto"}

    # ------------------------------------------------------------------
    # Prompt caching
    # ------------------------------------------------------------------

    @classmethod
    def _apply_cache_control(
        cls,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]] | None]:
        marker = {"type": "ephemeral"}

        if isinstance(system, str) and system:
            system = [{"type": "text", "text": system, "cache_control": marker}]
        elif isinstance(system, list) and system:
            system = list(system)
            system[-1] = {**system[-1], "cache_control": marker}

        new_msgs = list(messages)
        if len(new_msgs) >= 3:
            m = new_msgs[-2]
            c = m.get("content")
            if isinstance(c, str):
                new_msgs[-2] = {**m, "content": [{"type": "text", "text": c, "cache_control": marker}]}
            elif isinstance(c, list) and c:
                nc = list(c)
                nc[-1] = {**nc[-1], "cache_control": marker}
                new_msgs[-2] = {**m, "content": nc}

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": marker}

        return system, new_msgs, new_tools

    # ------------------------------------------------------------------
    # Build API kwargs
    # ------------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        supports_caching: bool = True,
        output_format: dict[str, Any] | None = None,
        service_tier: str | None = None,
        stop_sequences: list[str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_name = self._strip_prefix(model or self.default_model)
        system, anthropic_msgs = self._convert_messages(self._sanitize_empty_content(messages))
        anthropic_tools = self._convert_tools(tools)

        if supports_caching:
            system, anthropic_msgs, anthropic_tools = self._apply_cache_control(
                system, anthropic_msgs, anthropic_tools,
            )

        max_tokens = max(1, max_tokens)
        thinking_enabled = bool(reasoning_effort)

        # claude-opus-4-7 deprecated the `temperature` parameter entirely — the
        # API returns 400 if it is present, on any code path.
        omit_temperature = "opus-4-7" in model_name

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
        }

        if system:
            kwargs["system"] = system

        if reasoning_effort == "adaptive":
            # Adaptive thinking: model decides when and how much to think
            # Supported on claude-sonnet-4-6 and claude-opus-4-6.
            # Also auto-enables interleaved thinking between tool calls.
            kwargs["thinking"] = {"type": "adaptive"}
            if not omit_temperature:
                kwargs["temperature"] = 1.0
        elif thinking_enabled:
            budget_map = {"low": 1024, "medium": 4096, "high": max(8192, max_tokens), "max": max(16384, max_tokens)}
            budget = budget_map.get(reasoning_effort.lower(), 4096)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["max_tokens"] = max(max_tokens, budget + 4096)
            if not omit_temperature:
                kwargs["temperature"] = 1.0
        elif not omit_temperature:
            kwargs["temperature"] = temperature

        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            tc = self._convert_tool_choice(tool_choice, thinking_enabled)
            if tc:
                kwargs["tool_choice"] = tc

        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        if output_format is not None:
            kwargs["output_format"] = output_format
        if service_tier is not None:
            kwargs["service_tier"] = service_tier
        if stop_sequences is not None:
            kwargs["stop_sequences"] = stop_sequences
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        return kwargs

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_usage(u: Any) -> dict[str, int]:
        """Extract usage dict from a ``Message.usage`` object or similar."""
        usage: dict[str, int] = {}
        if not u:
            return usage

        input_tokens = u.input_tokens
        cache_creation = u.cache_creation_input_tokens or 0
        cache_read = u.cache_read_input_tokens or 0
        total_prompt_tokens = input_tokens + cache_creation + cache_read
        usage = {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": u.output_tokens,
            "total_tokens": total_prompt_tokens + u.output_tokens,
        }
        for attr in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            val = getattr(u, attr, 0)
            if val:
                usage[attr] = val
        if cache_read:
            usage["cached_tokens"] = cache_read

        ot = getattr(u, "output_tokens_details", None)
        if ot and getattr(ot, "thinking_tokens", 0):
            usage["thinking_tokens"] = ot.thinking_tokens

        cc = getattr(u, "cache_creation", None)
        if cc:
            for attr in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
                val = getattr(cc, attr, 0) or 0
                if val:
                    usage[attr] = val

        stu = getattr(u, "server_tool_use", None)
        if stu:
            for attr in ("web_search_requests", "web_fetch_requests"):
                val = getattr(stu, attr, 0) or 0
                if val:
                    usage[attr] = val

        return usage

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        thinking_blocks: list[dict[str, Any]] = []
        redacted_count = 0

        for block in response.content:
            if isinstance(block, TextBlock):
                content_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))
            elif isinstance(block, ThinkingBlock):
                thinking_blocks.append({
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                })
            elif isinstance(block, RedactedThinkingBlock):
                redacted_count += 1
            elif isinstance(block, ServerToolUseBlock):
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        if redacted_count:
            thinking_blocks.append({
                "type": "thinking",
                "thinking": f"[{redacted_count} redacted thinking block(s)]",
            })

        stop_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "pause_turn": "pause",
            "refusal": "refusal",
        }
        finish_reason = stop_map.get(response.stop_reason or "") or "stop"

        usage = AnthropicProvider._extract_usage(response.usage)

        content = "".join(content_parts) or None

        # Fallback: some providers (e.g. MiniMax Anthropic endpoint) may return
        # tool calls as XML/text in the content field instead of structured
        # ToolUseBlock.  Parse them out so the LLM doesn't see the raw XML in
        # history and learn to reproduce it on the next turn.
        if not tool_calls and content and detect_unparsed_tool_calls(content):
            extracted, cleaned = extract_xml_tool_calls(content)
            tool_calls.extend(extracted)
            content = cleaned

        sd = getattr(response, "stop_details", None)
        if isinstance(sd, RefusalStopDetails) and sd.explanation and not content:
            content = f"[Refusal: {sd.explanation}]"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            thinking_blocks=thinking_blocks or None,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    async def _iter_with_timeout(stream: Any, timeout: float) -> Any:
        """Wrap ``AsyncMessageStream.__aiter__`` with per-event timeout."""
        stream_iter = stream.__aiter__()
        while True:
            try:
                yield await asyncio.wait_for(stream_iter.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        output_format: dict[str, Any] | None = None,
        service_tier: str | None = None,
        stop_sequences: list[str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
            output_format=output_format,
            service_tier=service_tier,
            stop_sequences=stop_sequences,
            extra_body=extra_body,
        )
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                response = await stream.get_final_message()
            return self._parse_response(response)
        except Exception as e:
            return self._handle_error(e)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        output_format: dict[str, Any] | None = None,
        service_tier: str | None = None,
        stop_sequences: list[str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
            output_format=output_format,
            service_tier=service_tier,
            stop_sequences=stop_sequences,
            extra_body=extra_body,
        )
        idle_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "900"))
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                final_message = None
                try:
                    async for event in self._iter_with_timeout(stream, idle_timeout_s):
                        if isinstance(event, TextEvent):
                            if on_content_delta:
                                await on_content_delta(event.text)
                        elif isinstance(event, ThinkingEvent):
                            if on_reasoning_delta:
                                await on_reasoning_delta(event.thinking)
                        elif isinstance(event, ParsedMessageStopEvent):
                            final_message = event.message
                except IndexError:
                    logger.warning(
                        "Anthropic stream IndexError (likely thinking block "
                        "signature_delta at wrong index); "
                        "falling back to accumulated snapshot"
                    )
                    final_message = stream.current_message_snapshot

                if final_message is None:
                    return LLMResponse(
                        content="Error: stream ended without message_stop event",
                        finish_reason="error",
                    )

                return self._parse_response(final_message)
        except asyncio.TimeoutError:
            logger.warning("Anthropic stream timed out after {}s", idle_timeout_s)
            return LLMResponse(
                content=f"Error calling LLM: stream stalled for more than {idle_timeout_s} seconds",
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            if isinstance(e, APIStatusError) and e.status_code >= 500:
                body = e.body or {}
                error_obj = body.get("error", {}) if isinstance(body, dict) else {}
                error_msg = (
                    error_obj.get("message", "") if isinstance(error_obj, dict) else str(error_obj)
                ) or str(body)
                self._dump_prompt_on_error(
                    kwargs.get("messages", []),
                    e.status_code,
                    error_msg,
                    model or self.default_model,
                )
            return self._handle_error(e)

    def get_default_model(self) -> str:
        return self.default_model
