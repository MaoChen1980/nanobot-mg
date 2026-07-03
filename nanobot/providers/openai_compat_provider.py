"""OpenAI-compatible provider for all non-Anthropic LLM APIs."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
import json_repair
from loguru import logger

if os.environ.get("LANGFUSE_SECRET_KEY") and importlib.util.find_spec("langfuse"):
    from langfuse.openai import AsyncOpenAI
else:
    if os.environ.get("LANGFUSE_SECRET_KEY"):
        import logging
        logging.getLogger(__name__).warning(
            "LANGFUSE_SECRET_KEY is set but langfuse is not installed; "
            "install with `pip install langfuse` to enable tracing"
        )
    from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers._tool_call_parser import (
    _short_tool_id,
    extract_xml_tool_calls as _extract_xml_tool_calls,
)
from nanobot.providers.openai_responses import (
    consume_sdk_stream,
    convert_messages,
    convert_tools,
    parse_response_output,
)

if TYPE_CHECKING:
    from nanobot.providers.registry import ProviderSpec

_ALLOWED_MSG_KEYS = frozenset({
    "role", "content", "tool_calls", "tool_call_id", "name",
    "reasoning_content", "reasoning_details", "extra_content",
})
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively deep-merge override into base. Does not mutate inputs."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
_DEFAULT_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/HKUDS/nanobot",
    "X-OpenRouter-Title": "nanobot",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
_KIMI_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.5",
    "kimi-k2.6",
    "k2.6-code-preview",
})
_OPENAI_COMPAT_REQUEST_TIMEOUT_S = 120.0

# Maps ProviderSpec.thinking_style → extra_body builder.
# Each builder takes a bool (thinking_enabled) and returns the dict to
# merge into extra_body, keeping the style→wire-format mapping in one place.
_THINKING_STYLE_MAP: dict[str, Any] = {
    "thinking_type": lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
    "enable_thinking": lambda on: {"enable_thinking": on},
    "reasoning_split": lambda on: {"reasoning_split": on},
}


def _is_kimi_thinking_model(model_name: str) -> bool:
    """Return True if model_name refers to a Kimi thinking-capable model.

    Supports two forms:
    - Exact match: e.g. kimi-k2.5 / kimi-k2.6 in _KIMI_THINKING_MODELS
    - Slug match:  moonshotai/kimi-k2.5 -> the part after the last "/"
                   is checked against _KIMI_THINKING_MODELS

    This covers both the native Moonshot provider (bare slug) and
    OpenRouter-style names (``"publisher/slug"``).
    """
    name = model_name.lower()
    if name in _KIMI_THINKING_MODELS:
        return True
    if "/" in name and name.rsplit("/", 1)[1] in _KIMI_THINKING_MODELS:
        return True
    return False


def _openai_compat_timeout_s() -> float:
    """Return the bounded request timeout used for OpenAI-compatible providers."""
    return _float_env("NANOBOT_OPENAI_COMPAT_TIMEOUT_S", _OPENAI_COMPAT_REQUEST_TIMEOUT_S)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive {}={!r}; using {}", name, raw, default)
        return default
    return value

def _get(obj: Any, key: str) -> Any:
    """Get a value from dict or object attribute, returning None if absent."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """Try to coerce *value* to a dict; return None if not possible or empty."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value if value else None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict) and dumped:
            return dumped
    return None


_STANDARD_TC_KEYS: frozenset[str] = frozenset({
    "id", "type", "function", "index",
})


def _extract_tc_extras(tc: Any) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Extract (extra_content, provider_specific_fields, fn_provider_specific_fields).

    Captures Gemini ``extra_content`` verbatim and any non-standard keys on
    the tool-call / function.  Expects normalized dicts (caller normalizes
    at the boundary).
    """
    extra_content = _coerce_dict(_get(tc, "extra_content"))

    tc_dict = _coerce_dict(tc)
    prov = None
    fn_prov = None
    if tc_dict is not None:
        leftover = {k: v for k, v in tc_dict.items()
                    if k not in _STANDARD_TC_KEYS and k != "extra_content" and v is not None}
        if leftover:
            prov = leftover
        fn = _coerce_dict(tc_dict.get("function"))
        if fn is not None:
            fn_prov = _coerce_dict(fn.get("provider_specific_fields"))

    return extra_content, prov, fn_prov


def _uses_openrouter_attribution(spec: "ProviderSpec | None", api_base: str | None) -> bool:
    """Apply Nanobot attribution headers to OpenRouter requests by default."""
    if spec and spec.name == "openrouter":
        return True
    return bool(api_base and "openrouter" in api_base.lower())


_RESPONSES_FAILURE_THRESHOLD = 3
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes

def _is_direct_openai_base(api_base: str | None) -> bool:
    """Return True for direct OpenAI endpoints, not generic OpenAI-compatible gateways."""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


def _responses_circuit_key(
    model: str | None,
    default_model: str,
    reasoning_effort: str | None,
) -> str:
    model_name = (model or default_model).lower()
    effort = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
    return f"{model_name}:{effort}"


def _validate_tool_sequence(messages: list[dict[str, Any]]) -> None:
    """Walk all messages, validate tool_call/tool_result pairing, log mismatches."""
    declared: list[tuple[str, int]] = []
    results_seen: list[tuple[str, int]] = []
    orphans: list[str] = []

    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                tid = tc.get("id") if isinstance(tc, dict) else None
                if tid:
                    declared.append((tid, i))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                results_seen.append((tid, i))
                if not any(dt == tid for dt, _ in declared):
                    orphans.append(f"  msg[{i}] tool result has no matching assistant tool_call")

    unmatched_declared = list(declared)
    for rtid, ri in results_seen:
        for j, (dtid, di) in enumerate(unmatched_declared):
            if dtid == rtid:
                unmatched_declared.pop(j)
                break

    missing_results = []
    for tid, asst_idx in unmatched_declared:
        missing_results.append(f"  msg[{asst_idx}] tool_call '{tid[:12]}...' has no matching tool result")

    seen_ids: dict[str, list[int]] = {}
    dupe_warnings: list[str] = []
    for tid, idx in declared:
        if tid in seen_ids:
            seen_ids[tid].append(idx)
        else:
            seen_ids[tid] = [idx]
    for tid, indices in seen_ids.items():
        if len(indices) > 1:
            dupe_warnings.append(f"  tool_call_id appears in {len(indices)} assistant messages")

    total = len(messages)
    if orphans or missing_results or dupe_warnings:
        parts = []
        if orphans:
            parts.append("Orphan tool results:\n" + "\n".join(orphans))
        if missing_results:
            parts.append("Missing tool results:\n" + "\n".join(missing_results))
        if dupe_warnings:
            parts.append("Duplicate IDs:\n" + "\n".join(dupe_warnings))
        logger.warning("TOOL_SEQ_MISMATCH in {} messages:\n{}", total, "\n\n".join(parts))
    else:
        logger.info("TOOL_SEQ_OK ({} messages, {} tool_calls, {} tool_results)",
                     total, len(declared), len(results_seen))


class OpenAICompatProvider(LLMProvider):
    """Unified provider for all OpenAI-compatible APIs.

    Receives a resolved ``ProviderSpec`` from the caller — no internal
    registry lookups needed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        spec: ProviderSpec | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._extra_body = extra_body or {}
        self._spec = spec

        if api_key and spec and spec.env_key:
            self._setup_env(api_key, api_base)

        effective_base = api_base or (spec.default_api_base if spec else None) or None
        self._effective_base = effective_base
        default_headers = {"x-session-affinity": uuid.uuid4().hex}
        if _uses_openrouter_attribution(spec, effective_base):
            default_headers.update(_DEFAULT_OPENROUTER_HEADERS)
        if extra_headers:
            default_headers.update(extra_headers)

        # Many LLM API servers (local and cloud) close idle HTTP connections
        # before the client-side keepalive expires.  When two LLM calls happen
        # seconds apart, the second call may grab a now-dead pooled connection,
        # causing a transient error.  Disabling keepalive avoids this by
        # opening a fresh connection for each request.  The overhead of a TCP
        # handshake is negligible compared to LLM response times.
        timeout_s = _openai_compat_timeout_s()
        # Set httpx read timeout to the overall request timeout so httpx does
        # NOT preemptively raise ReadTimeout during long streaming gaps
        # (e.g. reasoning models that pause >30s between chunks). The actual
        # per-chunk idle guard is asyncio.wait_for in chat_stream().
        idle_read_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "30"))
        t = httpx.Timeout(timeout_s, read=timeout_s, pool=None)
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(keepalive_expiry=0),
            timeout=t,
        )

        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=effective_base,
            default_headers=default_headers,
            max_retries=0,
            timeout=t,
            http_client=http_client,
        )

        # Responses API circuit breaker: skip after repeated failures,
        # probe again after _RESPONSES_PROBE_INTERVAL_S seconds.
        self._responses_failures: dict[str, int] = {}
        self._responses_tripped_at: dict[str, float] = {}

    def _setup_env(self, api_key: str, api_base: str | None) -> None:
        """Set environment variables based on provider spec."""
        spec = self._spec
        if not spec or not spec.env_key:
            return
        if spec.is_gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key).replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    @classmethod
    def _apply_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject cache_control markers for prompt caching."""
        cache_marker = {"type": "ephemeral"}
        new_messages = list(messages)

        def _mark(msg: dict[str, Any]) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": cache_marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                nc[-1] = {**nc[-1], "cache_control": cache_marker}
                return {**msg, "content": nc}
            return msg

        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0])
        if len(new_messages) >= 3:
            new_messages[-2] = _mark(new_messages[-2])

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": cache_marker}
        return new_messages, new_tools

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize to a provider-safe 9-char alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    @staticmethod
    def _normalize_tool_call_arguments(arguments: Any) -> str:
        """Force function.arguments into a valid JSON object string."""
        if isinstance(arguments, str):
            stripped = arguments.strip()
            if not stripped:
                return "{}"
            try:
                parsed = json_repair.loads(stripped)
            except Exception:
                return "{}"
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
            return "{}"
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        return "{}"

    @staticmethod
    def _coerce_content_to_string(content: Any) -> str | None:
        """Coerce block/list content into plain text for strict string-only APIs."""
        if content is None or isinstance(content, str):
            return content
        text = OpenAICompatProvider._extract_text_content(content)
        if isinstance(text, str) and text:
            return text
        try:
            dumped = json.dumps(content, ensure_ascii=False)
        except Exception:
            dumped = str(content)
        return dumped or "(empty)"

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys, normalize tool_call IDs, deduplicate tool results."""
        sanitized = LLMProvider._sanitize_request_messages(messages, _ALLOWED_MSG_KEYS)
        id_map: dict[str, str] = {}
        force_string_content = bool(self._spec and self._spec.name == "deepseek")

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            return id_map.setdefault(value, self._normalize_tool_call_id(value))

        seen_tool_ids: set[str] = set()

        for clean in sanitized:
            if isinstance(clean.get("tool_calls"), list):
                normalized = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized.append(tc)
                        continue
                    tc_clean = dict(tc)
                    tc_clean["id"] = map_id(tc_clean.get("id"))
                    function = tc_clean.get("function")
                    if isinstance(function, dict):
                        function_clean = dict(function)
                        if "arguments" in function_clean:
                            function_clean["arguments"] = self._normalize_tool_call_arguments(
                                function_clean.get("arguments")
                            )
                        else:
                            function_clean["arguments"] = "{}"
                        tc_clean["function"] = function_clean
                    normalized.append(tc_clean)
                clean["tool_calls"] = normalized
                if clean.get("role") == "assistant":
                    clean["content"] = None
                seen_tool_ids.clear()
            if "tool_call_id" in clean and clean["tool_call_id"]:
                normalized_id = map_id(clean["tool_call_id"])
                clean["tool_call_id"] = normalized_id
                if clean.get("role") == "tool":
                    if normalized_id in seen_tool_ids:
                        clean["_skip"] = True
                    else:
                        seen_tool_ids.add(normalized_id)
            if (
                force_string_content
                and not (clean.get("role") == "assistant" and clean.get("tool_calls"))
            ):
                clean["content"] = self._coerce_content_to_string(clean.get("content"))
        result = self._enforce_role_alternation(sanitized)
        result = [m for m in result if not m.get("_skip")]

        # Full-sequence validation: find any tool_call/tool_result mismatch
        # across ALL messages, not just the last N.
        _validate_tool_sequence(result)

        # Sanitize surrogates before they reach the HTTP client's UTF-8 encoder.
        result = [LLMProvider._replace_surrogates(m) for m in result]

        return result

    # ------------------------------------------------------------------
    # Build kwargs
    # ------------------------------------------------------------------

    @staticmethod
    def _supports_temperature(
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        """Return True when the model accepts a temperature parameter.

        GPT-5 family and reasoning models (o1/o3/o4) reject temperature
        when reasoning_effort is set to anything other than ``"none"``.
        """
        if reasoning_effort and reasoning_effort.lower() != "none":
            return False
        name = model_name.lower()
        return not any(token in name for token in ("gpt-5", "o1", "o3", "o4"))

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        spec = self._spec

        # Apply provider-specific default for reasoning_effort when not explicitly set.
        if reasoning_effort is None and spec and spec.default_reasoning_effort is not None:
            reasoning_effort = spec.default_reasoning_effort

        if spec and spec.supports_prompt_caching:
            model_name = model or self.default_model
            if any(model_name.lower().startswith(k) for k in ("anthropic/", "claude")):
                messages, tools = self._apply_cache_control(messages, tools)

        if spec and spec.strip_model_prefix:
            model_name = model_name.split("/")[-1]

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
        }

        # GPT-5 and reasoning models (o1/o3/o4) reject temperature when
        # reasoning_effort is active.  Only include it when safe.
        if self._supports_temperature(model_name, reasoning_effort):
            kwargs["temperature"] = temperature

        if spec and getattr(spec, "supports_max_completion_tokens", False):
            kwargs["max_completion_tokens"] = max(1, max_tokens)
        else:
            kwargs["max_tokens"] = max(1, max_tokens)

        if spec:
            model_lower = model_name.lower()
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    break

        # Normalize reasoning_effort into a semantic form (OpenAI vocab)
        # used for internal decisions, and a wire form actually sent out.
        # "minimum" is accepted as a DashScope-native alias for "minimal".
        semantic_effort: str | None = None
        if isinstance(reasoning_effort, str):
            semantic_effort = reasoning_effort.lower()
            if semantic_effort == "minimum":
                semantic_effort = "minimal"

        wire_effort = reasoning_effort
        if spec and spec.name == "dashscope" and semantic_effort == "minimal":
            # DashScope accepts none/minimum/low/medium/high/xhigh; "minimal" 400s.
            wire_effort = "minimum"

        # Some providers (MiniMax) don't accept reasoning_effort as a
        # top-level parameter — they use their own thinking mechanism
        # (reasoning_split in extra_body) instead.
        skip_reasoning_effort = bool(spec and spec.thinking_style == "reasoning_split")
        if wire_effort and not skip_reasoning_effort:
            kwargs["reasoning_effort"] = wire_effort

        # Provider-specific thinking parameters.
        # Only sent when reasoning_effort is explicitly configured so that
        # the provider default is preserved otherwise.
        # The mapping is driven by ProviderSpec.thinking_style so that adding
        # a new provider never requires touching this function.
        #
        # For reasoning_split providers (MiniMax): always set reasoning_split
        # when tools are present, so tool calls use the standard structured
        # format instead of raw XML in content.
        if spec and spec.thinking_style == "reasoning_split" and tools:
            kwargs.setdefault("extra_body", {}).update({"reasoning_split": True})
        elif spec and spec.thinking_style and reasoning_effort is not None:
            thinking_enabled = semantic_effort not in ("none", "minimal")
            extra = _THINKING_STYLE_MAP.get(spec.thinking_style, lambda _: None)(thinking_enabled)
            if extra:
                kwargs.setdefault("extra_body", {}).update(extra)

        # Model-level thinking injection for Kimi thinking-capable models.
        # Strip any provider prefix (e.g. "moonshotai/") before the set lookup
        # so that OpenRouter-style names like "moonshotai/kimi-k2.5" are handled
        # identically to bare names like "kimi-k2.5".
        if reasoning_effort is not None and _is_kimi_thinking_model(model_name):
            thinking_enabled = semantic_effort not in ("none", "minimal")
            kwargs.setdefault("extra_body", {}).update(
                {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
            )
            # Preserve historical reasoning across multi-turn conversations.
            # Without this, Kimi trims old reasoning to save tokens.
            if thinking_enabled:
                kwargs.setdefault("extra_body", {}).setdefault("thinking", {})["keep"] = "all"

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        # Backfill reasoning_content on legacy assistant messages.
        # DeepSeek V4 (and potentially others) rejects thinking-mode
        # requests that contain assistant messages without reasoning_content
        # — even on turns that had no tool calls. This happens when a
        # session was started with a non-thinking model or without
        # reasoning_effort, then the user switches thinking mode on
        # mid-session. Injecting an empty string satisfies the API
        # without altering semantics (the model treats it as "no
        # thinking happened on that turn").
        thinking_active = (
            (spec and spec.thinking_style and reasoning_effort is not None
             and semantic_effort not in ("none", "minimal"))
            or (reasoning_effort is not None and _is_kimi_thinking_model(model_name)
                and semantic_effort not in ("none", "minimal"))
        )
        if thinking_active:
            logger.info(
                "_build_kwargs thinking_active=true spec={} reasoning_effort={} semantic_effort={}",
                spec.name if spec else "none",
                reasoning_effort,
                semantic_effort,
            )
            # reasoning_split providers (MiniMax) don't accept reasoning_content
            # in request messages — they use extra_body reasoning_split instead.
            skip_rc_backfill = bool(spec and spec.thinking_style == "reasoning_split")
            new_msgs = []
            for msg in kwargs["messages"]:
                if msg.get("role") == "assistant":
                    if skip_rc_backfill:
                        # Remove reasoning_content entirely — MiniMax doesn't
                        # recognize this field in request messages.
                        new_msgs.append({k: v for k, v in msg.items() if k != "reasoning_content"})
                    elif not msg.get("reasoning_content"):
                        # reasoning_content backfill for ALL assistant messages
                        # (including tool-call ones). Some providers reject thinking-mode
                        # requests with assistant messages missing reasoning_content.
                        new_msgs.append({**msg, "reasoning_content": " "})
                    else:
                        new_msgs.append(msg)
                else:
                    new_msgs.append(msg)
            kwargs["messages"] = new_msgs

        # MiniMax: _sanitize_messages unconditionally sets content=None on ALL
        # assistant tool-call messages.  MiniMax reverses the usual
        # convention and rejects content=null — even when thinking mode is not
        # active.  Run this fix unconditionally so that non-thinking requests
        # also get non-null content on historical tool-call messages.
        if spec and spec.thinking_style == "reasoning_split":
            for msg in kwargs["messages"]:
                if msg.get("tool_calls") and msg.get("content") is None:
                    msg["content"] = " "

        # GLM: preserved thinking across multi-turn (clear_thinking: False).
        # Without this, GLM clears historical reasoning each turn.
        if spec and spec.name == "zhipu" and thinking_active:
            kwargs.setdefault("extra_body", {}).setdefault("thinking", {})["clear_thinking"] = False

        # Merge user-configured extra_body last so it can override or extend internal defaults
        if self._extra_body:
            existing = kwargs.get("extra_body", {})
            kwargs["extra_body"] = _deep_merge(existing, self._extra_body)

        logger.info("_build_kwargs: model={} extra_body={} max_tokens={} temperature={} reasoning_effort={}",
                     model_name, kwargs.get("extra_body"), kwargs.get("max_tokens"),
                     kwargs.get("temperature"), kwargs.get("reasoning_effort"))

        # Pre-send dump: show last 15 messages with roles, content length, tool_call count
        dump_msgs = kwargs["messages"][-15:]
        dump_lines = []
        for i, m in enumerate(dump_msgs):
            role = m.get("role", "?")
            c = m.get("content")
            content_preview = f"len={len(c)}" if isinstance(c, str) else ("None" if c is None else f"type={type(c).__name__}")
            tc_ids = []
            for tc in (m.get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("id"):
                    tc_ids.append(str(tc["id"])[:8])
            tr_id = str(m.get("tool_call_id", ""))[:8] if m.get("tool_call_id") else ""
            extra = ""
            if tc_ids:
                extra += f" tc=[{','.join(tc_ids)}]"
            if tr_id:
                extra += f" tr={tr_id}"
            if m.get("reasoning_content"):
                extra += " rc=present"
            dump_lines.append(f"  msg[-{15-i}] {role:>9} content={content_preview}{extra}")
        logger.debug("PRE_SEND_MSGS (last 15):\n{}", "\n".join(dump_lines))
        return kwargs

    def _should_use_responses_api(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """Use Responses API only for direct OpenAI requests that benefit from it."""
        if self._spec and self._spec.name not in ("openai", "github_copilot"):
            return False
        if self._spec is None or self._spec.name != "github_copilot":
            if not _is_direct_openai_base(self._effective_base):
                return False

        model_name = (model or self.default_model).lower()
        wants = False
        if reasoning_effort and reasoning_effort.lower() != "none":
            wants = True
        elif any(token in model_name for token in ("gpt-5", "o1", "o3", "o4")):
            wants = True
        if not wants:
            return False

        # Circuit breaker: skip after repeated failures, probe periodically.
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        failures = self._responses_failures.get(key, 0)
        if failures >= _RESPONSES_FAILURE_THRESHOLD:
            tripped = self._responses_tripped_at.get(key, 0.0)
            if (time.monotonic() - tripped) < _RESPONSES_PROBE_INTERVAL_S:
                return False
            # Half-open: allow one probe attempt
        return True

    def _record_responses_failure(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        count = self._responses_failures.get(key, 0) + 1
        self._responses_failures[key] = count
        if count >= _RESPONSES_FAILURE_THRESHOLD:
            self._responses_tripped_at[key] = time.monotonic()
            logger.warning(
                "Responses API circuit open for {} — falling back to Chat Completions",
                key,
            )

    def _record_responses_success(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        self._responses_failures.pop(key, None)
        self._responses_tripped_at.pop(key, None)

    @staticmethod
    def _should_fallback_from_responses_error(e: Exception) -> bool:
        """Fallback only for likely Responses API compatibility errors."""
        response = getattr(e, "response", None)
        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code not in {400, 404, 422}:
            return False

        body = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        body_text = str(body).lower() if body is not None else ""
        compatibility_markers = (
            "responses",
            "response api",
            "max_output_tokens",
            "instructions",
            "previous_response",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized request argument",
        )
        return any(marker in body_text for marker in compatibility_markers)

    def _build_responses_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a Responses API body for direct OpenAI requests."""
        model_name = model or self.default_model
        if self._spec and self._spec.strip_model_prefix:
            model_name = model_name.split("/")[-1]
        sanitized_messages = self._sanitize_messages(self._sanitize_empty_content(messages))
        instructions, input_items = convert_messages(sanitized_messages)

        body: dict[str, Any] = {
            "model": model_name,
            "instructions": instructions or None,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "store": False,
            "stream": False,
        }

        if self._supports_temperature(model_name, reasoning_effort):
            body["temperature"] = temperature

        if reasoning_effort and reasoning_effort.lower() != "none":
            body["reasoning"] = {"effort": reasoning_effort}
            body["include"] = ["reasoning.encrypted_content"]

        if tools:
            body["tools"] = convert_tools(tools)
            body["tool_choice"] = tool_choice or "auto"

        return body

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return None

    @staticmethod
    def _normalize(value: Any) -> dict[str, Any] | None:
        """Normalize SDK object to dict, or pass through dict as-is.

        Deep-recursive: nested Pydantic models, SimpleNamespace, mocks, and
        other ``__dict__``-backed objects are all flattened to plain dicts.
        Call once at the boundary so downstream code only handles dicts.
        """
        return LLMProvider._normalize(value)

    @classmethod
    def _extract_text_content(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_map = cls._maybe_mapping(item)
                if item_map:
                    text = item_map.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(value)

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        """Extract token usage from an OpenAI-compatible response.

        Normalises provider-specific ``cached_tokens`` fields under a single key.
        """
        response_map = cls._normalize(response)
        if not response_map:
            return {}

        usage_map = cls._normalize(response_map.get("usage"))
        if not usage_map:
            return {}

        result = {
            "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_map.get("completion_tokens") or 0),
            "total_tokens": int(usage_map.get("total_tokens") or 0),
        }

        # --- cached_tokens (normalised across providers) ---
        for path in (
            ("prompt_tokens_details", "cached_tokens"),  # OpenAI/Zhipu/MiniMax/Qwen/Mistral/xAI
            ("cached_tokens",),                          # StepFun/Moonshot (top-level)
            ("prompt_cache_hit_tokens",),                # DeepSeek/SiliconFlow
        ):
            cached = cls._get_nested_int(usage_map, path)
            if cached:
                result["cached_tokens"] = cached
                break

        # --- reasoning_tokens (OpenAI SDK v2.38+) ---
        rt = cls._get_nested_int(usage_map, ("completion_tokens_details", "reasoning_tokens"))
        if rt:
            result["reasoning_tokens"] = rt

        return result

    @staticmethod
    def _get_nested_int(obj: dict[str, Any] | None, path: tuple[str, ...]) -> int:
        """Drill into a normalized dict by *path* segments and return an ``int``."""
        current = obj
        for segment in path:
            if not isinstance(current, dict):
                return 0
            current = current.get(segment)
        return int(current or 0) if current is not None else 0

    def _parse(self, response: Any) -> LLMResponse:
        if isinstance(response, str):
            return LLMResponse(content=response, finish_reason="stop")

        response_map = self._normalize(response)
        if not response_map:
            return LLMResponse(
                content="Error: API returned unsupported response type.",
                finish_reason="error",
            )

        choices = response_map.get("choices") or []
        if not choices:
            content = self._extract_text_content(
                response_map.get("content") or response_map.get("output_text")
            )
            reasoning_content = self._extract_text_content(
                response_map.get("reasoning_content")
            )
            # MiniMax: extract reasoning_details from no-choices response
            reasoning_details = None
            if self._spec and self._spec.name in ("minimax", "minimax_cn"):
                rd = response_map.get("reasoning_details")
                if isinstance(rd, list):
                    reasoning_details = rd
                    if not reasoning_content:
                        parts = []
                        for item in rd:
                            if isinstance(item, dict) and item.get("type") == "reasoning.text":
                                t = item.get("text")
                                if t:
                                    parts.append(t)
                        if parts:
                            reasoning_content = "\n".join(parts)
            if content is not None:
                return LLMResponse(
                    content=content,
                    reasoning_content=reasoning_content,
                    reasoning_details=reasoning_details,
                    finish_reason=str(response_map.get("finish_reason") or "stop"),
                    usage=self._extract_usage(response_map),
                )
            return LLMResponse(
                content="Error: API returned empty choices.",
                finish_reason="error",
            )

        # --- Normal path: has choices ---
        choice0 = choices[0]
        msg0 = choice0.get("message") or {}
        content = self._extract_text_content(msg0.get("content"))
        finish_reason = str(choice0.get("finish_reason") or "stop")

        # StepFun: fallback to reasoning field when content is empty
        if not content and msg0.get("reasoning") and self._spec and self._spec.reasoning_as_content:
            content = self._extract_text_content(msg0.get("reasoning"))
        reasoning_content = msg0.get("reasoning_content")
        if not reasoning_content and msg0.get("reasoning"):
            reasoning_content = self._extract_text_content(msg0.get("reasoning"))

        # MiniMax: capture original reasoning_details array + extract text
        reasoning_details = None
        if self._spec and self._spec.name in ("minimax", "minimax_cn"):
            rd = msg0.get("reasoning_details")
            if isinstance(rd, list):
                reasoning_details = rd
                if not reasoning_content:
                    parts = []
                    for item in rd:
                        if isinstance(item, dict) and item.get("type") == "reasoning.text":
                            t = item.get("text")
                            if t:
                                parts.append(t)
                    if parts:
                        reasoning_content = "\n".join(parts)

        # refusal
        if content is None and msg0.get("refusal"):
            content = f"[Refusal: {msg0['refusal']}]"

        # Collect tool calls + additional content/reasoning from all choices
        raw_tool_calls: list[Any] = []
        for ch in choices:
            m = ch.get("message") or {}
            tc_list = m.get("tool_calls")
            if isinstance(tc_list, list) and tc_list:
                raw_tool_calls.extend(tc_list)
                if ch.get("finish_reason") in ("tool_calls", "stop"):
                    finish_reason = str(ch["finish_reason"])
            if not content:
                content = self._extract_text_content(m.get("content"))
            if not content and m.get("reasoning") and self._spec and self._spec.reasoning_as_content:
                content = self._extract_text_content(m.get("reasoning"))
            if not reasoning_content:
                reasoning_content = m.get("reasoning_content")
                if not reasoning_content and m.get("reasoning"):
                    reasoning_content = self._extract_text_content(m.get("reasoning"))
                if not reasoning_content and self._spec and self._spec.name in ("minimax", "minimax_cn"):
                    rd = m.get("reasoning_details")
                    if isinstance(rd, list):
                        if not reasoning_details:
                            reasoning_details = rd
                        parts = []
                        for item in rd:
                            if isinstance(item, dict) and item.get("type") == "reasoning.text":
                                t = item.get("text")
                                if t:
                                    parts.append(t)
                        if parts:
                            reasoning_content = "\n".join(parts)

        # Parse tool calls
        tool_calls = []
        for tc in raw_tool_calls:
            tc_type = tc.get("type")
            if tc_type == "custom":
                c = tc.get("custom")
                if c:
                    raw_args = c.get("input", "{}")
                    try:
                        args = json_repair.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {"input": raw_args}
                    tool_calls.append(ToolCallRequest(
                        id=_short_tool_id(),
                        name=c.get("name", ""),
                        arguments=args if isinstance(args, dict) else {},
                    ))
                continue
            fn = tc.get("function") or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json_repair.loads(args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            tool_calls.append(ToolCallRequest(
                id=_short_tool_id(),
                name=str(fn.get("name") or ""),
                arguments=args if isinstance(args, dict) else {},
                extra_content=ec,
                provider_specific_fields=prov,
                function_provider_specific_fields=fn_prov,
            ))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._extract_usage(response_map),
            reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            reasoning_details=reasoning_details,
        )

    @classmethod
    def _accumulate_stream(cls, chunks: list[Any]) -> dict[str, Any]:
        """Accumulate streaming chunks into a consolidated result dict.

        Accepts pre-normalized dicts (Phase 1) or raw SDK objects.  Returns a
        dict with keys: ``content_parts``, ``reasoning_parts``, ``tc_bufs``,
        ``finish_reason``, ``usage``.
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        for chunk in chunks:
            if isinstance(chunk, str):
                content_parts.append(chunk)
                continue

            chunk_map = cls._normalize(chunk)
            if not chunk_map:
                continue

            choices = chunk_map.get("choices") or []
            if not choices:
                usage = cls._extract_usage(chunk_map) or usage
                text = cls._extract_text_content(
                    chunk_map.get("content") or chunk_map.get("output_text")
                )
                if text:
                    content_parts.append(text)
                continue

            choice = choices[0]
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta") or {}

            text = cls._extract_text_content(delta.get("content"))
            if text:
                content_parts.append(text)
            elif delta.get("refusal"):
                content_parts.append(f"[Refusal: {delta['refusal']}]")

            text = cls._extract_text_content(delta.get("reasoning_content"))
            if not text:
                text = cls._extract_text_content(delta.get("reasoning"))
            if text:
                reasoning_parts.append(text)

            for idx, tc in enumerate(delta.get("tool_calls") or []):
                tc_index: int = tc.get("index") if tc.get("index") is not None else idx
                buf = tc_bufs.setdefault(tc_index, {
                    "id": "", "name": "", "arguments": "",
                    "extra_content": None, "prov": None, "fn_prov": None,
                })
                tc_id = tc.get("id")
                if tc_id:
                    buf["id"] = str(tc_id)
                fn = tc.get("function")
                if fn is not None:
                    fn_name = fn.get("name")
                    if fn_name:
                        buf["name"] = str(fn_name)
                    fn_args = fn.get("arguments")
                    if fn_args:
                        buf["arguments"] += str(fn_args)
                ec, prov, fn_prov = _extract_tc_extras(tc)
                if ec:
                    buf["extra_content"] = ec
                if prov:
                    buf["prov"] = prov
                if fn_prov:
                    buf["fn_prov"] = fn_prov

            usage = cls._extract_usage(chunk_map) or usage

        return {
            "content_parts": content_parts,
            "reasoning_parts": reasoning_parts,
            "tc_bufs": tc_bufs,
            "finish_reason": finish_reason,
            "usage": usage,
        }

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        acc = cls._accumulate_stream(chunks)
        content = "".join(acc["content_parts"]) or None
        reasoning = "".join(acc["reasoning_parts"]) or None
        return LLMResponse(
            content=content,
            tool_calls=[
                ToolCallRequest(
                    id=b["id"] or _short_tool_id(),
                    name=b["name"],
                    arguments=json_repair.loads(b["arguments"]) if b["arguments"] else {},
                    extra_content=b.get("extra_content"),
                    provider_specific_fields=b.get("prov"),
                    function_provider_specific_fields=b.get("fn_prov"),
                )
                for b in acc["tc_bufs"].values()
            ],
            finish_reason=acc["finish_reason"],
            usage=acc["usage"],
            reasoning_content=reasoning,
        )

    @classmethod
    def _extract_error_metadata(cls, e: Exception) -> dict[str, Any]:
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        payload = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        error_type, error_code = LLMProvider._extract_error_type_code(payload)

        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name or "protocol" in error_name:
            error_kind = "connection"

        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_retry_after_s": cls._extract_retry_after_from_headers(headers),
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _handle_error(
        e: Exception,
        *,
        spec: ProviderSpec | None = None,
        api_base: str | None = None,
    ) -> LLMResponse:
        body = (
            getattr(e, "doc", None)
            or getattr(e, "body", None)
            or getattr(getattr(e, "response", None), "text", None)
        )
        body_text = body if isinstance(body, str) else str(body) if body is not None else ""
        err_str = str(e).strip()
        if body_text.strip():
            msg = f"Error: {body_text.strip()[:500]}"
        elif err_str:
            msg = f"Error calling LLM: {err_str}"
        else:
            # e.g. httpx.ReadTimeout with empty str() representation
            msg = f"Error calling LLM: {e.__class__.__name__}"

        text = f"{body_text} {e}".lower()
        if spec and spec.is_local and ("502" in text or "connection" in text or "refused" in text):
            msg += (
                "\nHint: this is a local model endpoint. Check that the local server is reachable at "
                f"{api_base or spec.default_api_base}, and if you are using a proxy/tunnel, make sure it "
                "can reach your local Ollama/vLLM service instead of routing localhost through the remote host."
            )

        response = getattr(e, "response", None)
        retry_after = LLMProvider._extract_retry_after_from_headers(getattr(response, "headers", None))
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)

        error_meta = OpenAICompatProvider._extract_error_metadata(e)

        # 429 rate_limit_error with no server-provided retry_after → default
        if retry_after is None and error_meta.get("error_status_code") == 429:
            error_type = (error_meta.get("error_type") or "").lower()
            if "rate_limit" in error_type or "rate_limit" in msg:
                retry_after = LLMProvider._RATE_LIMIT_RETRY_SECONDS

        logger.exception(
            "OpenAI-compat API error: status={}, type={}, code={}, msg={}",
            error_meta.get("error_status_code"),
            error_meta.get("error_type"),
            error_meta.get("error_code"),
            msg[:200],
        )

        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            **error_meta,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    result = parse_response_output(await self._client.responses.create(**body))
                    self._record_responses_success(model, reasoning_effort)
                    return result
                except Exception as responses_error:
                    if self._spec and self._spec.name == "github_copilot":
                        # Copilot gateway exposes GPT-5/o-series only via /responses;
                        # falling back to /chat/completions cannot succeed and would
                        # hide the real error.
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            result = self._parse(await self._client.chat.completions.create(**kwargs))

            # Some providers (MiniMax) return tool calls as XML text in ``content``
            # instead of structured ``tool_calls``.  Parse in-place instead of
            # retrying — prompt-based correction is unreliable.
            if not result.tool_calls and result.content:
                tool_calls, cleaned = _extract_xml_tool_calls(result.content)
                if tool_calls:
                    result = LLMResponse(
                        content=cleaned,
                        tool_calls=tool_calls,
                        finish_reason="tool_calls",
                        usage=result.usage,
                        reasoning_content=result.reasoning_content,
                        reasoning_details=result.reasoning_details,
                    )

            return result
        except Exception as e:
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

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
    ) -> LLMResponse:
        spec_idle = (self._spec.stream_idle_timeout if self._spec else 0)
        if spec_idle:
            idle_timeout_s = spec_idle
        else:
            idle_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "30"))
        stream = None
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    body["stream"] = True
                    stream = await self._client.responses.create(**body)

                    async def _timed_stream():
                        stream_iter = stream.__aiter__()
                        while True:
                            try:
                                yield await asyncio.wait_for(
                                    stream_iter.__anext__(),
                                    timeout=idle_timeout_s,
                                )
                            except StopAsyncIteration:
                                break

                    content, tool_calls, finish_reason, usage, reasoning_content = await consume_sdk_stream(
                        _timed_stream(),
                        on_content_delta,
                    )
                    self._record_responses_success(model, reasoning_effort)
                    return LLMResponse(
                        content=content or None,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        usage=usage,
                        reasoning_content=reasoning_content,
                    )
                except Exception as responses_error:
                    if self._spec and self._spec.name == "github_copilot":
                        # Copilot gateway exposes GPT-5/o-series only via /responses;
                        # falling back to /chat/completions cannot succeed and would
                        # hide the real error.
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

            stream = await self._client.chat.completions.create(**kwargs)
            chunks: list[Any] = []
            stream_iter = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=idle_timeout_s,
                    )
                except StopAsyncIteration:
                    break
                chunk_map = self._normalize(chunk) or chunk
                chunks.append(chunk_map)
                if isinstance(chunk_map, dict):
                    choices = chunk_map.get("choices")
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if text and on_content_delta:
                            await on_content_delta(text)
                        reasoning = delta.get("reasoning_content")
                        if reasoning and on_reasoning_delta:
                            await on_reasoning_delta(reasoning)
            result = self._parse_chunks(chunks)
            # Same in-place XML parsing as chat() — handles MiniMax plain-text
            # tool calls even in streaming responses.
            if not result.tool_calls and result.content:
                tool_calls, cleaned = _extract_xml_tool_calls(result.content)
                if tool_calls:
                    result = LLMResponse(
                        content=cleaned,
                        tool_calls=tool_calls,
                        finish_reason="tool_calls",
                        usage=result.usage,
                        reasoning_content=result.reasoning_content,
                        reasoning_details=result.reasoning_details,
                    )
            return result
        except asyncio.TimeoutError:
            logger.warning("OpenAI-compat stream timed out after {}s", idle_timeout_s)
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{idle_timeout_s} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            # Close the stream so the underlying httpx connection is
            # properly released back to the pool (or discarded if broken).
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

    def get_default_model(self) -> str:
        return self.default_model
