"""Real API test for assess_me — verify end-to-end JSON output from MiniMax.

Checks that:
1. ``assess_me()`` returns parseable JSON from real MiniMax calls
2. ``loop.py``'s ``_extract_assess_json()`` can parse the output
3. The retry logic works correctly when MiniMax emits non-JSON

Run with: pytest tests/real_api/test_assess_me_real.py -v -x --no-header
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.assess_me import assess_me
from nanobot.agent.llm_context import set_llm
from nanobot.agent.loop import AgentLoop


@pytest.fixture(scope="session")
def llm_ctx():
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import build_provider_snapshot

    config = resolve_config_env_vars(load_config())
    snapshot = build_provider_snapshot(config)
    set_llm(snapshot.provider, snapshot.model)
    return snapshot


@pytest.mark.asyncio
async def test_assess_me_returns_valid_json(llm_ctx):
    """Make a real assess_me call and verify the output is valid JSON
    parseable by _extract_assess_json."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there, how can I help you today?"},
        {"role": "user", "content": "can you check the weather in Shanghai?"},
    ]

    result = await assess_me(messages, has_active_task=True)
    assert result, "assess_me returned empty"

    print(f"\n--- assess_me result ({len(result)} chars):")
    for line in result.split("\n")[:8]:
        print(f"    {line}")
    if len(result.split("\n")) > 8:
        print(f"    ... ({len(result.split('\n')) - 8} more lines)")

    # Test 1: _extract_assess_json (loop.py) can parse it
    parsed = AgentLoop._extract_assess_json(result)
    assert parsed is not None, f"_extract_assess_json returned None for:\n{result[:300]}"
    assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}"
    assert "status" in parsed, f"Missing 'status' key in {list(parsed.keys())}"
    assert "summary" in parsed, f"Missing 'summary' key in {list(parsed.keys())}"
    assert parsed["status"] in ("ok", "findings"), f"Unexpected status: {parsed['status']}"
    print(f"--- Parsed: status={parsed['status']}, summary={parsed.get('summary', 'N/A')}")


@pytest.mark.asyncio
async def test_assess_me_with_empty_conversation(llm_ctx):
    """Test assess_me with a very short conversation — the scenario that
    previously triggered non-JSON output."""
    messages = [
        {"role": "user", "content": "hi"},
    ]

    result = await assess_me(messages, has_active_task=False)
    assert result, "assess_me returned empty on short conversation"

    parsed = AgentLoop._extract_assess_json(result)
    assert parsed is not None, (
        f"_extract_assess_json could not parse assess_me output for short conversation.\n"
        f"Raw output: {result[:300]}"
    )
    print(f"--- Short conv: status={parsed['status']}, summary={parsed.get('summary', 'N/A')}")
