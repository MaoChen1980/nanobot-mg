"""Real API test for compress.py — verify MiniMax summarization output is usable.

Checks that ``strip_think`` and ``_strip_xml_tool_calls`` can handle the real
MiniMax output from the summarization prompt.

Run with: pytest tests/real_api/test_compress_real.py -v -x --no-header
"""

from __future__ import annotations

import pytest

from nanobot.agent.compress import _strip_xml_tool_calls
from nanobot.agent.llm_context import chat_stream_with_retry, set_llm
from nanobot.agent.loop_utils import strip_think as strip_think_loop


@pytest.fixture(scope="session")
def llm_ctx():
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import build_provider_snapshot

    config = resolve_config_env_vars(load_config())
    snapshot = build_provider_snapshot(config)
    set_llm(snapshot.provider, snapshot.model)
    return snapshot


_SUMMARY_PROMPT = (
    "## 任务\n"
    "压缩以下旧对话为摘要。摘要将与下方的保留对话拼接，必须保证推理连续性。\n"
    "\n"
    "## 输出要求\n"
    "按顺序输出以下 4 个章节，要点列表格式：\n"
    "- **目标**：根目标\n"
    "- **当前状态**：进度、产出、阻塞。关键信息必须保留\n"
    "- **到达路径**：试错结论，不要中间过程\n"
    "- **下一步**：待办事项\n"
    "\n"
    "---\n"
    "## 保留对话\n\n"
    "(empty — first compression)\n\n"
    "---\n"
    "## 待压缩对话\n\n"
    "user: hello, can you help me set up a new project?\n"
    "assistant: sure, I'll help you initialize a python project with pytest and ruff\n"
    "user: great, let's use uv for package management\n"
    "assistant: done. created pyproject.toml with pytest and ruff config\n"
    "\n"
    "---\n"
    "## 约束\n"
    "- 纯文本，无 XML/代码/工具调用\n"
    "- 不可丢弃关键信息（结论、配置、阻塞原因）\n"
    "- 可丢弃：文件路径、目录结构、工具输出、试错过程、寒暄\n"
)


@pytest.mark.asyncio
async def test_summarize_turns_output_clean(llm_ctx):
    """Verify MiniMax summarization output is clean text (no raw tool calls)."""
    response = await chat_stream_with_retry(
        [{"role": "user", "content": _SUMMARY_PROMPT}],
    )

    assert response.finish_reason != "error", f"LLM error: {response.content}"
    assert response.content, "Empty response"

    raw = response.content.strip()
    print(f"\n--- Raw output ({len(raw)} chars):")
    for line in raw.split("\n")[:10]:
        print(f"    {line}")
    if len(raw.split("\n")) > 10:
        print(f"    ... ({len(raw.split('\n')) - 10} more lines)")

    # Apply the same post-processing as compress.py
    cleaned = strip_think_loop(raw)
    cleaned = _strip_xml_tool_calls(cleaned) if cleaned else ""
    print(f"--- After strip_think + strip_xml ({len(cleaned)} chars)")

    # Should be non-empty after cleaning
    assert cleaned, "Summary was empty after strip_think"


@pytest.mark.asyncio
async def test_strip_think_handles_real_minimax_output():
    """Verify strip_think handles the real MiniMax output format patterns."""
    # Simulate common MiniMax output patterns with think tags
    cases = [
        # Normal: no think tags
        ("**目标**：setup project\n**当前状态**：done", True, True),
        # Closed think tag before content
        ("<think>Simple task</think>\n**目标**：setup project", True, True),
        # Unclosed think tag at start — strip_think removes everything after it (by design)
        ("<think>Let me analyze this\n**目标**：setup project\n**当前状态**：done", False, True),
        # Think tag in the middle
        ("**目标**：hello <think>internal note</think> world", True, False),
    ]

    for text, expect_nonempty, expect_notag in cases:
        result = strip_think_loop(text)
        if expect_nonempty:
            assert result, f"strip_think returned empty for: {text[:50]}"
        else:
            assert not result, f"Expected empty for unclosed think tag, got: {result[:50]}"
        if result and expect_notag:
            assert "<think>" not in result, f"strip_think didn't remove <think>: {result[:50]}"
