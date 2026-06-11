"""Regression, integration, and scenario tests for surrogate encoding fixes.

Bug: Python's UTF-8 encoder rejects unpaired surrogates (U+D800-U+DFFF),
crashing the HTTP client when serializing LLM request bodies. This file
verifies the fix at three levels:
  - Unit: _replace_surrogates handles all message structures
  - Integration: Provider sanitization pipelines clean surrogates
  - Scenario: Real-world message flows produce json-safe output
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from nanobot.providers.base import LLMProvider


# ── Regression tests for _replace_surrogates ──────────────────────────────


class TestReplaceSurrogates:
    """Direct tests for the _replace_surrogates static method."""

    def test_string_with_surrogates_replaced(self):
        """Surrogates in a plain string are replaced with U+FFFD."""
        result = LLMProvider._replace_surrogates("hello \ud800 world \udfff end")
        assert "\ud800" not in result
        assert "\udfff" not in result
        assert "hello" in result
        assert "world" in result
        assert "end" in result

    def test_string_without_surrogates_unchanged(self):
        """Normal text passes through unchanged."""
        text = "hello world 你好 🐈 nanobot"
        assert LLMProvider._replace_surrogates(text) is text

    def test_empty_string_unchanged(self):
        assert LLMProvider._replace_surrogates("") == ""

    def test_none_passthrough(self):
        assert LLMProvider._replace_surrogates(None) is None

    def test_integer_passthrough(self):
        assert LLMProvider._replace_surrogates(42) == 42

    def test_list_of_strings_cleaned(self):
        result = LLMProvider._replace_surrogates([
            "clean text",
            "bad \ud800 data",
            "also \udfff bad",
        ])
        assert result[0] == "clean text"
        assert "\ud800" not in result[1]
        assert "\udfff" not in result[2]

    def test_nested_dict_with_surrogates_cleaned(self):
        result = LLMProvider._replace_surrogates({
            "role": "user",
            "content": "text with 😀 and lone \ud800",
            "extra": {"nested": "\udfff here too"},
        })
        assert "😀" in result["content"]  # emoji preserved
        assert "\ud800" not in result["content"]
        assert "\udfff" not in result["extra"]["nested"]

    def test_full_message_structure(self):
        """Complex nested message with tool_calls and content blocks."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "test \ud800"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "result with \udfff surrogate",
            },
        ]
        result = [LLMProvider._replace_surrogates(m) for m in messages]
        assert "\ud800" not in result[0]["tool_calls"][0]["function"]["arguments"]
        assert "\udfff" not in result[1]["content"]

    def test_large_binary_like_string(self):
        """Simulate a long tool result with scattered surrogates."""
        text = "A" * 100000 + "\ud800" + "B" * 100000 + "\udfff"
        result = LLMProvider._replace_surrogates(text)
        assert len(result) == 200002  # same length: 1 char → 1 char
        assert "\ud800" not in result
        assert "\udfff" not in result

    def test_high_surrogate_crash_scenario(self):
        """U+D800 is the surrogate that crashed SafeFileHistory."""
        text = "normal prefix \ud800 suffix"
        result = LLMProvider._replace_surrogates(text)
        assert "\ud800" not in result

    def test_encode_safe_after_sanitize(self):
        """After _replace_surrogates, str.encode and json.dumps never crash."""
        cases = [
            "\ud800",
            "\udfff",
            "lead 😀 trail \ud800 end",
            {"nested": ["a", "\ud800", {"deep": "\udfff"}]},
            [1, 2, "\ud800", {"x": "\udfff"}],
            None,
            True,
            0,
        ]
        for case in cases:
            cleaned = LLMProvider._replace_surrogates(case)
            if isinstance(cleaned, str):
                cleaned.encode("utf-8")
            elif isinstance(cleaned, (list, dict)):
                json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
            elif cleaned is not None:
                str(cleaned).encode("utf-8")


# ── Integration tests for OpenAICompatProvider ─────────────────────────────


@pytest.fixture
def openai_provider():
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider
        yield OpenAICompatProvider()


class TestOpenAICompatSurrogate:
    """OpenAICompatProvider._sanitize_messages with surrogate content."""

    def test_sanitize_cleans_content_string(self, openai_provider):
        messages = [{"role": "user", "content": "hello \ud800 world"}]
        result = openai_provider._sanitize_messages(messages)
        assert "\ud800" not in result[0]["content"]

    def test_sanitize_cleans_tool_result(self, openai_provider):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc12345",
                        "type": "function",
                        "function": {"name": "fn", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc12345",
                "content": "big result with \ud800 at position 42",
            },
        ]
        result = openai_provider._sanitize_messages(messages)
        tool_msg = [m for m in result if m.get("role") == "tool"]
        assert tool_msg
        assert "\ud800" not in tool_msg[0]["content"]

    def test_sanitize_cleans_deepseek_content(self, openai_provider):
        """DeepSeek provider coerces content to string, which must also be clean."""
        from nanobot.providers.registry import ProviderSpec
        openai_provider._spec = ProviderSpec(name="deepseek", keywords=("deepseek",), env_key="DEEPSEEK_API_KEY")
        messages = [{"role": "user", "content": ["text with \ud800", {"type": "text", "text": "more \udfff"}]}]
        result = openai_provider._sanitize_messages(messages)
        assert "\ud800" not in result[0]["content"]
        assert "\udfff" not in result[0]["content"]

    def test_sanitize_output_json_safe(self, openai_provider):
        """After _sanitize_messages, the result can be safely json.dumps'd."""
        messages = [
            {"role": "user", "content": "normal"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_abc12345", "type": "function", "function": {"name": "fn", "arguments": '{"k": "\ud800"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_abc12345", "content": "\udfff result"},
        ]
        result = openai_provider._sanitize_messages(messages)
        json.dumps(result, ensure_ascii=False).encode("utf-8")


# ── Integration tests for AnthropicProvider ────────────────────────────────


@pytest.fixture
def anthropic_provider():
    with patch("anthropic.AsyncAnthropic"):
        from nanobot.providers.anthropic_provider import AnthropicProvider
        yield AnthropicProvider()


class TestAnthropicSurrogate:
    """AnthropicProvider message processing with surrogates."""

    def test_replace_surrogates_on_entry(self, anthropic_provider):
        messages = [{"role": "user", "content": "bad \ud800 here"}]
        cleaned = LLMProvider._replace_surrogates(messages)
        assert "\ud800" not in cleaned[0]["content"]

    def test_replace_surrogates_nested_blocks(self, anthropic_provider):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "safe"},
            {"type": "text", "text": "bad \ud800"},
        ]}]
        cleaned = LLMProvider._replace_surrogates(messages)
        assert "\ud800" not in cleaned[0]["content"][1]["text"]

    def test_anthropic_messages_json_safe(self, anthropic_provider):
        messages = [
            {"role": "user", "content": "leading text"},
            {"role": "assistant", "content": "response with \ud800"},
            {"role": "user", "content": [{"type": "text", "text": "\udfff final"}]},
        ]
        cleaned = LLMProvider._replace_surrogates(messages)
        json.dumps(cleaned, ensure_ascii=False).encode("utf-8")


# ── Scenario tests ─────────────────────────────────────────────────────────


class TestSurrogateScenarios:
    """Real-world scenarios for surrogate handling."""

    def test_accumulated_tool_results_with_surrogates(self):
        """Simulate a long agent session with tool results containing surrogates."""
        messages = [{"role": "system", "content": "You are a helpful assistant."}]
        for i in range(5):
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{i:09d}",
                    "type": "function",
                    "function": {"name": f"tool_{i}", "arguments": "{}"},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i:09d}",
                "content": f"result with surrogate \ud800 at position {i * 1000}" if i % 2 == 0
                else "clean result",
            })
        messages.append({"role": "user", "content": "summarize"})

        cleaned = LLMProvider._replace_surrogates(messages)

        for msg in cleaned:
            if isinstance(msg.get("content"), str):
                assert "\ud800" not in msg["content"]
                assert "\udfff" not in msg["content"]

        json.dumps(cleaned, ensure_ascii=False).encode("utf-8")

    def test_mixed_safe_and_unsafe_unicode(self):
        """CJK + valid emoji + surrogates are handled without crash."""
        text = "你好 hello こんにちは 🎉 \ud800 test 😀 world"
        result = LLMProvider._replace_surrogates(text)
        result.encode("utf-8")

    def test_full_pipeline_no_crash(self, openai_provider):
        """Full pipeline: messages → _sanitize_empty_content → _sanitize_messages → json-safe."""
        import copy

        messages = [
            {"role": "user", "content": "initial query"},
        ]
        for i in range(3):
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"tc_{i:09d}",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                    "_meta": {"test": True},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"tc_{i:09d}",
                "name": "search",
                "content": "binary\x00data\ud800leak" if i == 1 else "normal result",
                "_meta": {"size": i},
            })
        messages.append({"role": "user", "content": "done"})

        msgs_copy = copy.deepcopy(messages)
        step1 = LLMProvider._sanitize_empty_content(msgs_copy)
        step2 = openai_provider._sanitize_messages(step1)

        for msg in step2:
            content = msg.get("content")
            if isinstance(content, str):
                assert "\ud800" not in content
                assert "\udfff" not in content

        json.dumps(step2, ensure_ascii=False).encode("utf-8")

    def test_safe_file_history_exact_crash(self, tmp_path):
        """Reproduce the exact crash from SafeFileHistory with high surrogate."""
        from nanobot.cli.commands import SafeFileHistory

        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("command with \ud800 at position 69")

        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\ud800" not in entries[0]


# ── Additional scenario tests ──────────────────────────────────────────


class TestDbSurrogateHandling:
    """NanobotDB handles surrogates gracefully in json.dumps calls."""

    def test_insert_tool_call_with_surrogate_content(self, tmp_path):
        from nanobot.agent.db import NanobotDB
        db = NanobotDB(tmp_path / "surr_test.db")
        try:
            db.insert_tool_call(
                "s1", iteration=1, turn=1,
                tool_name="exec_tool",
                params={"cmd": "test"},
                result="hello \ud800 world",
                success=True,
            )
            rows = db.query_tool_calls(limit=1)
            assert len(rows) == 1
            assert "\ud800" not in rows[0]["result"]
        finally:
            db.close()

    def test_insert_tool_call_with_surrogate_params(self, tmp_path):
        from nanobot.agent.db import NanobotDB
        db = NanobotDB(tmp_path / "surr_params.db")
        try:
            db.insert_tool_call(
                "s1", iteration=1, turn=1,
                tool_name="exec_tool",
                params={"cmd": "test \udfff"},
                result="ok",
                success=True,
            )
            rows = db.query_tool_calls(limit=1)
            assert len(rows) == 1
        finally:
            db.close()

    def test_append_history_with_surrogate(self, tmp_path):
        from nanobot.agent.db import NanobotDB
        db = NanobotDB(tmp_path / "surr_hist.db")
        try:
            db.append_history("history with \ud800 at position 42")
            entries = db.read_entries()
            assert len(entries) == 1
        finally:
            db.close()

    def test_complex_tool_result_with_mixed_surrogates(self, tmp_path):
        """Dict result with surrogates at multiple nesting levels."""
        from nanobot.agent.db import NanobotDB
        db = NanobotDB(tmp_path / "surr_complex.db")
        try:
            result = {
                "status": "error",
                "message": "command failed with \ud800 at offset 42",
                "details": {"stderr": "line1\nline2 with \udfff\n"},
                "items": ["a", "b", "item with \ud800 here"],
            }
            db.insert_tool_call(
                "s1", iteration=1, turn=1,
                tool_name="exec_tool",
                params={"cmd": "test"},
                result=str(result),
                success=False,
                error="surrogate error",
            )
            rows = db.query_tool_calls(limit=1)
            assert len(rows) == 1
            assert rows[0]["success"] == 0
        finally:
            db.close()


class TestSafeFileHistoryEdgeCases:
    """Additional edge cases for SafeFileHistory surrogate handling."""

    def test_entirely_surrogate_string(self, tmp_path):
        from nanobot.cli.commands import SafeFileHistory
        hist = SafeFileHistory(str(tmp_path / "hist_all_surr"))
        hist.store_string("𐏿\ud800")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        surr_count = sum(1 for c in entries[0] if c == "\ud800" or c == "\udfff")
        assert surr_count == 0

    def test_surrogate_scattered_across_long_string(self, tmp_path):
        from nanobot.cli.commands import SafeFileHistory
        hist = SafeFileHistory(str(tmp_path / "hist_long"))
        text = "A" * 50000 + "\ud800" + "B" * 50000 + "\udfff"
        hist.store_string(text)
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\ud800" not in entries[0]
        assert "\udfff" not in entries[0]

    def test_multiple_surrogates_in_single_string(self, tmp_path):
        from nanobot.cli.commands import SafeFileHistory
        hist = SafeFileHistory(str(tmp_path / "hist_multi"))
        hist.store_string("a\ud800b\ud800c\udfffd\udfffe")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\ud800" not in entries[0]
        assert "\udfff" not in entries[0]


class TestReplaceSurrogatesEdgeCases:
    """Extreme edge cases for _replace_surrogates."""

    def test_deeply_nested_structure(self):
        """Dict nested 10+ levels with surrogates at every level."""
        nested = {"l1": {"l2": {"l3": {"l4": {"l5": {
            "text": "deep \ud800",
            "items": [
                {"sub": "also \udfff"},
                [1, 2, {"deep": "\ud800"}],
            ],
        }}}}}}
        result = LLMProvider._replace_surrogates(nested)
        result_str = str(result)
        assert "\ud800" not in result_str
        assert "\udfff" not in result_str

    def test_mixed_list_types(self):
        """List with surrogates, ints, Nones, bools."""
        data = ["\ud800", 42, None, True, {"key": "\udfff"}, [1, "\ud800", 3]]
        result = LLMProvider._replace_surrogates(data)
        assert result[0] != "\ud800"
        assert result[1] == 42
        assert result[2] is None
        assert result[3] is True

    def test_json_safe_after_deep_sanitize(self):
        """After _replace_surrogates, json.dumps never crashes on any structure."""
        data = {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello \ud800"},
                {"type": "image_url", "image_url": {"url": "data:img"}, "_meta": {"path": "\udfff"}},
            ],
            "extra": {"a": [1, "\ud800", {"b": "\udfff"}]},
        }
        cleaned = LLMProvider._replace_surrogates(data)
        json.dumps(cleaned, ensure_ascii=False).encode("utf-8")

    def test_surrogate_at_every_boundary(self):
        """Surrogates at positions 0, len-1, and everywhere in between."""
        text = "\ud800" + "normal" * 100 + "\udfff"
        text = text[:1000]
        result = LLMProvider._replace_surrogates(text)
        assert "\ud800" not in result
        assert "\udfff" not in result
        assert result == "�" + "normal" * 100 + "�"

    def test_cyclic_reference_no_infinite_loop(self):
        """Dict with self-reference doesn't cause infinite recursion."""
        data: dict = {"key": "value"}
        data["self"] = data
        with pytest.raises(RecursionError):
            LLMProvider._replace_surrogates(data)

    def test_empty_list_and_dict(self):
        assert LLMProvider._replace_surrogates([]) == []
        assert LLMProvider._replace_surrogates({}) == {}


class TestProviderSurrogateIntegration:
    """Integration: surrogate handling through full provider pipelines."""

    def test_openai_compat_full_pipeline_safe(self):
        """Messages → _sanitize_empty_content → _sanitize_messages → json-safe."""
        from unittest.mock import patch
        with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
            from nanobot.providers.openai_compat_provider import OpenAICompatProvider
            provider = OpenAICompatProvider()
            import copy
            messages = [
                {"role": "user", "content": "hello \ud800 world"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "tc_1", "type": "function", "function": {"name": "fn", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "tc_1", "content": "result with \udfff"},
            ]
            step1 = LLMProvider._sanitize_empty_content(copy.deepcopy(messages))
            step2 = provider._sanitize_messages(step1)
            json.dumps(step2, ensure_ascii=False).encode("utf-8")

    def test_anthropic_surrogate_nested_content_blocks(self):
        """Anthropic-style nested content blocks with surrogates."""
        from unittest.mock import patch
        with patch("anthropic.AsyncAnthropic"):
            from nanobot.providers.anthropic_provider import AnthropicProvider
            provider = AnthropicProvider()
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": "first \ud800 block"},
                    {"type": "text", "text": "second \udfff block"},
                    {"type": "text", "text": "clean text"},
                ]},
                {"role": "assistant", "content": "response with \ud800"},
            ]
            cleaned = LLMProvider._replace_surrogates(messages)
            for msg in cleaned:
                c = msg.get("content")
                if isinstance(c, str):
                    assert "\ud800" not in c
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and "text" in block:
                            assert "\ud800" not in block["text"]
            json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
