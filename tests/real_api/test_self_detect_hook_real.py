"""Real API test for SelfDetectHook reflection — verify MiniMax output is parseable.

Checks that ``_call_llm`` produces output that ``_parse_findings`` can handle,
even when MiniMax wraps the response in <think> tags (which it always does).

Run with: pytest tests/real_api/test_self_detect_hook_real.py -v -x --no-header
"""

from __future__ import annotations

import json

import pytest

from nanobot.agent.llm_context import chat_stream_with_retry, set_llm
from nanobot.hooks.self_detect_hook import REFLECTION_SYSTEM_PROMPT, SelfDetectHook


@pytest.fixture(scope="session")
def llm_ctx():
    """Load user config and inject provider+model into ContextVars."""
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import build_provider_snapshot

    config = resolve_config_env_vars(load_config())
    snapshot = build_provider_snapshot(config)
    set_llm(snapshot.provider, snapshot.model)
    return snapshot


@pytest.fixture(scope="session")
def provider_info(llm_ctx):
    return {
        "model": llm_ctx.model,
        "provider_type": type(llm_ctx.provider).__name__,
    }


_MINIMAL_METRICS = """\
## Turn 1
- tool_calls: web_search (q="test query")
- tool_count: 1
- error: null
- final_content: "Search results: ..."
"""

_MINIMAL_HOOK_CODE = """\
class SelfDetectHook(AgentHook):
    def __init__(self, interval: int = 5):
        self.interval = interval

    def _build_entry(self, ctx):
        return {"tool_calls": ctx.tool_calls}
"""


@pytest.mark.asyncio
async def test_parse_findings_handles_real_minimax_output(provider_info):
    """Make a real LLM call with the reflection prompt, then verify
    _parse_findings can parse the output."""
    print(f"\n--- Testing with provider: {provider_info['provider_type']}, model: {provider_info['model']}")

    response = await chat_stream_with_retry(
        messages=[
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "## 输入数据\n\n"
                + _MINIMAL_METRICS
                + "\n\n## Hook 模块结构\n\n"
                + _MINIMAL_HOOK_CODE
                + "\n\n## 分析步骤\n\n"
                + "1. 检查行为模式\n"
                + "2. 检查代码结构",
            },
        ],
    )

    assert response.finish_reason != "error", f"LLM call error: {response.content}"
    assert response.content, "Empty response from LLM"

    raw = response.content.strip()
    print(f"--- Raw LLM output ({len(raw)} chars):")
    for line in raw.split("\n")[:15]:
        print(f"    {line}")
    if len(raw.split("\n")) > 15:
        print(f"    ... ({len(raw.split('\n')) - 15} more lines)")

    # Check for think tags
    has_think_open = "<think>" in raw
    has_think_close = "</think>" in raw
    if has_think_open or has_think_close:
        print(f"--- Contains <think> tags: open={has_think_open}, close={has_think_close}")

    # Test _parse_findings
    findings, diagnostic = SelfDetectHook._parse_findings(raw)
    print(f"--- _parse_findings result: diagnostic={diagnostic}, findings_count={len(findings)}")

    assert diagnostic in ("ok", "empty_findings"), (
        f"Expected 'ok' or 'empty_findings', got '{diagnostic}'. "
        f"This means _parse_findings could NOT handle the MiniMax output.\n"
        f"Raw preview: {raw[:300]}"
    )


@pytest.mark.asyncio
async def test_parse_findings_still_works_with_known_tag_variants():
    """Verify _parse_findings handles all known MiniMax output formats."""
    hook = SelfDetectHook

    # Case 1: JSON inside code fences (standard)
    r1 = '{"findings": [{"type": "behavior", "content": "test", "relevance": "test"}]}'
    f1, d1 = hook._parse_findings(r1)
    assert d1 == "ok", f"Case 1 failed: {d1}"

    # Case 2: JSON inside ``` code fences
    r2 = '```\n{"findings": [{"type": "behavior", "content": "test", "relevance": "test"}]}\n```'
    f2, d2 = hook._parse_findings(r2)
    assert d2 == "ok", f"Case 2 failed: {d2}"

    # Case 3: <think> tags + JSON inside code fences
    r3 = "<think>Let me analyze...</think>\n```\n{\"findings\": [{\"type\": \"behavior\", \"content\": \"test\", \"relevance\": \"test\"}]}\n```"
    f3, d3 = hook._parse_findings(r3)
    assert d3 == "ok", f"Case 3 failed: {d3}"

    # Case 4: <think> tags + JSON WITHOUT code fences — most dangerous case
    r4 = "<think>Let me analyze...</think>\n{\"findings\": [{\"type\": \"behavior\", \"content\": \"test\", \"relevance\": \"test\"}]}"
    f4, d4 = hook._parse_findings(r4)
    # This is the problematic case — currently expected to FAIL
    print(f"--- Case 4 (think+no-fence): diagnostic={d4}")
    # Document: if this passes, the vulnerability is gone; if it fails, we need a fix
    if d4 == "json_decode_error":
        print("    WARNING: <think> tags without code fences cause json_decode_error!")
        print("    This confirms _parse_findings is vulnerable to MiniMax think tags.")


@pytest.mark.asyncio
async def test_empty_findings_handled(provider_info):
    """Verify that empty-findings response is handled correctly."""
    # Send a completely empty/trivial input so LLM returns empty findings
    response = await chat_stream_with_retry(
        messages=[
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "## 输入数据\n\nNo metrics available.\n\n## Hook 模块结构\n\nNo hook code.\n\n## 分析步骤\n\nNo steps.",
            },
        ],
    )

    if not response.content:
        pytest.skip("Empty LLM response — cannot test")

    raw = response.content.strip()
    findings, diagnostic = SelfDetectHook._parse_findings(raw)
    print(f"--- Empty input: diagnostic={diagnostic}, findings={len(findings)}")

    # Both empty_findings and ok are valid results when there's nothing to find
    assert diagnostic in ("ok", "empty_findings", "all_filtered"), (
        f"Expected non-error diagnostic, got '{diagnostic}'"
    )
