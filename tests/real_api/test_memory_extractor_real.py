"""Real API test for MemoryExtractor — verify JSON extraction from MiniMax output.

Checks that ``_extract_json_from_llm_output`` can parse the real LLM output
from the analysis prompt, which requests JSON inside ```json code fences.

Run with: pytest tests/real_api/test_memory_extractor_real.py -v -x --no-header
"""

from __future__ import annotations

import json

import pytest

from nanobot.agent.llm_context import chat_stream_with_retry, set_llm
from nanobot.agent.memory_extractor import MemoryExtractor
from nanobot.utils.prompt_templates import render_template


@pytest.fixture(scope="session")
def llm_ctx():
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import build_provider_snapshot

    config = resolve_config_env_vars(load_config())
    snapshot = build_provider_snapshot(config)
    set_llm(snapshot.provider, snapshot.model)
    return snapshot


_MINIMAL_CONVERSATION = (
    "[Snapshot saved at: 2026-06-24T12:00:00+08:00]\n"
    "[Each message may contain its own timestamp field.]\n\n"
    '[{"role": "user", "content": "update the readme"}, '
    '{"role": "assistant", "content": "updated the readme with new install instructions"}]'
)


@pytest.mark.asyncio
async def test_extract_json_from_llm_output_handles_real_minimax(llm_ctx):
    """Call the extractor analysis LLM with minimal data and verify
    _extract_json_from_llm_output can parse the output."""
    prompt = render_template("agent/extractor_analysis.md", workspace_path="/tmp/test")
    response = await chat_stream_with_retry(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": _MINIMAL_CONVERSATION},
        ],
    )

    assert response.finish_reason != "error", f"LLM call error: {response.content}"
    assert response.content, "Empty response"

    raw = response.content.strip()
    print(f"\n--- Raw output ({len(raw)} chars):")
    for line in raw.split("\n")[:12]:
        print(f"    {line}")
    if len(raw.split("\n")) > 12:
        print(f"    ... ({len(raw.split('\n')) - 12} more lines)")

    clean = MemoryExtractor._extract_json_from_llm_output(raw)
    parsed = json.loads(clean)

    assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}"
    assert "findings" in parsed, f"Missing 'findings' key in {list(parsed.keys())}"
    print(f"--- Parsed successfully: {len(parsed['findings'])} findings")
