"""Tests for _tool_call_parser.py — extract_xml_tool_calls."""

import pytest

from nanobot.providers._tool_call_parser import extract_xml_tool_calls


def test_empty_content():
    tc, cleaned = extract_xml_tool_calls("")
    assert tc == []
    assert cleaned == ""


def test_none_content():
    tc, cleaned = extract_xml_tool_calls(None)
    assert tc == []
    assert cleaned is None


def test_plain_text_no_tool_calls():
    text = "Hello, world. This is a normal response."
    tc, cleaned = extract_xml_tool_calls(text)
    assert tc == []
    assert cleaned == text


def test_metadata_lines_stripped():
    text = (
        "[Tool: web_search | https://example.com]\n"
        "Here is the result.\n"
        "[Source: docs | nanobot/README.md]"
    )
    tc, cleaned = extract_xml_tool_calls(text)
    assert tc == []
    assert cleaned == "Here is the result."


def test_invoke_xml_format():
    content = (
        'Before text. '
        '<invoke name="search">'
        '<parameter name="query">weather</parameter>'
        '</invoke>'
        ' After text.'
    )
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) == 1
    assert tc[0].name == "search"
    assert tc[0].arguments == {"query": "weather"}
    assert cleaned == "Before text.  After text."


def test_invoke_tool_attribute():
    content = (
        '<invoke tool="read_file">'
        '<parameter name="path">/tmp/x</parameter>'
        '</invoke>'
    )
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) == 1
    assert tc[0].name == "read_file"
    assert cleaned is None


def test_dict_format():
    content = 'Some text. {tool => "search", args => { --q "hello" }} Trailing.'
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) == 1
    assert tc[0].name == "search"
    assert tc[0].arguments == {"q": "hello"}
    assert cleaned == "Some text.  Trailing."


def test_args_format():
    content = '{tool name="web_fetch" args="--url https://example.com --format text"}'
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) == 1
    assert tc[0].name == "web_fetch"


def test_tc_wrapper_with_multiple_calls():
    content = (
        "Text before.\n"
        "[TOOL_CALL]\n"
        '{tool name="search" args="--q hello"}'
        '{tool name="read" args="--path /tmp/x"}'
        "[/TOOL_CALL]\n"
        "Text after."
    )
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) >= 2
    assert tc[0].name == "search"
    assert tc[1].name == "read"
    assert "Text before." in (cleaned or "")
    assert "Text after." in (cleaned or "")


def test_content_without_any_tool_patterns():
    """Regression: normal content should not be modified when no tool calls exist."""
    text = "I understand your request. Let me work on that."
    tc, cleaned = extract_xml_tool_calls(text)
    assert tc == []
    assert cleaned == text


def test_mixed_metadata_and_tool_call():
    """Metadata lines are stripped first, then tool calls are extracted."""
    content = (
        "[Tool: search | result]\n"
        '<invoke name="read"><parameter name="path">/tmp/x</parameter></invoke>'
    )
    tc, cleaned = extract_xml_tool_calls(content)
    assert len(tc) == 1
    assert tc[0].name == "read"
    assert cleaned is None or cleaned.strip() == ""
