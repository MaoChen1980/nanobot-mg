"""Real API test for OpenAICompatProvider (minimax_cn) — verify assess_me JSON output.

MiniMax-M2.7 via the openai_compat backend uses ``reasoning_split`` in
extra_body. This test verifies that the assess_me prompt returns parseable
JSON through this backend too.

Run with: pytest tests/real_api/test_openai_compat_real.py -v -x --no-header
"""

from __future__ import annotations

import pytest

from nanobot.agent.assess_me import assess_me
from nanobot.agent.llm_context import set_llm
from nanobot.agent.loop import AgentLoop
from nanobot.providers.registry import find_by_name


@pytest.fixture(scope="session")
def llm_ctx():
    """Load config and create a minimax_cn (openai_compat) provider."""
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    config = resolve_config_env_vars(load_config())

    pc = getattr(config.providers, "minimax_cn", None)
    if not pc or not pc.api_key:
        pytest.skip("No minimax_cn provider config found")

    spec = find_by_name("minimax_cn")
    provider = OpenAICompatProvider(
        api_key=pc.api_key,
        api_base=pc.api_base,
        default_model="MiniMax-M2.7",
        extra_headers=pc.extra_headers,
        extra_body=pc.extra_body,
        spec=spec,
    )

    set_llm(provider, provider.default_model)
    return {"provider": "OpenAICompat", "model": provider.default_model}


@pytest.mark.asyncio
async def test_assess_me_through_openai_compat(llm_ctx):
    """Call assess_me through the openai_compat backend and verify JSON output."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "what's the weather in Shanghai?"},
    ]

    result = await assess_me(messages, has_active_task=True)
    assert result, "assess_me returned empty"

    print(f"\n--- assess_me result ({len(result)} chars):")
    for line in result.split("\n")[:6]:
        print(f"    {line}")
    if len(result.split("\n")) > 6:
        print(f"    ... ({len(result.split('\n')) - 6} more lines)")

    parsed = AgentLoop._extract_assess_json(result)
    assert parsed is not None, f"_extract_assess_json returned None:\n{result[:300]}"
    assert "status" in parsed, f"Missing 'status' key"
    assert parsed["status"] in ("ok", "findings"), f"Unexpected status: {parsed['status']}"
    print(f"--- Parsed: status={parsed['status']}, summary={parsed.get('summary', 'N/A')}")


@pytest.mark.asyncio
async def test_assess_me_short_conversation(llm_ctx):
    """Short conversation — the case that previously triggered non-JSON output."""
    result = await assess_me([{"role": "user", "content": "hi"}], has_active_task=False)
    assert result, "assess_me returned empty"

    parsed = AgentLoop._extract_assess_json(result)
    assert parsed is not None, (
        f"_extract_assess_json could not parse output.\n"
        f"Raw: {result[:300]}"
    )
    print(f"--- Short conv: status={parsed['status']}")
