import json

import pytest

from nanobot.agent.memory_extractor import MemoryExtractor


def _extract(text: str) -> str:
    return MemoryExtractor._extract_json_from_llm_output(text)


def test_fence_basic() -> None:
    raw = '```\n{"name": "foo", "value": 42}\n```'
    assert json.loads(_extract(raw)) == {"name": "foo", "value": 42}


def test_fence_nested_triple_backtick_in_string() -> None:
    """Nested ``` inside a JSON string value must not break extraction."""
    raw = '''```
{
  "skills": [
    {
      "name": "test-skill",
      "content": "Some text with ``` inside it"
    }
  ]
}
```'''
    parsed = json.loads(_extract(raw))
    assert parsed["skills"][0]["name"] == "test-skill"
    assert "```" in parsed["skills"][0]["content"]


def test_fence_without_closing() -> None:
    """LLM sometimes omits the closing ```."""
    raw = '```\n{"key": "value"}\n'
    assert json.loads(_extract(raw)) == {"key": "value"}


def test_no_fence() -> None:
    raw = '{"a": 1, "b": 2}'
    assert json.loads(_extract(raw)) == {"a": 1, "b": 2}


def test_think_tag() -> None:
    raw = '<think>some reasoning</think>\n{"a": 1}'
    assert json.loads(_extract(raw)) == {"a": 1}


def test_think_tag_with_fence() -> None:
    raw = '<think>reasoning</think>\n```\n{"a": 1}\n```'
    assert json.loads(_extract(raw)) == {"a": 1}


def test_no_json() -> None:
    raw = "just some text"
    result = _extract(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_empty() -> None:
    assert _extract("") == ""


def test_think_only() -> None:
    assert _extract("<think>reasoning</think>") == ""


def test_multiline_json() -> None:
    raw = '{\n"a": 1,\n"b": 2\n}'
    assert json.loads(_extract(raw)) == {"a": 1, "b": 2}


def test_text_before_json() -> None:
    raw = 'Here is the result: {"skills": [{"name": "x"}]}'
    assert json.loads(_extract(raw)) == {"skills": [{"name": "x"}]}
