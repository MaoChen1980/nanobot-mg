"""Shared parser for unparsed tool calls embedded in LLM response content.

Some providers (MiniMax, etc.) return tool calls as XML or custom text
formats inside the ``content`` field instead of the structured ``tool_calls``
field.  Both the OpenAI-compat and Anthropic providers need to detect and
extract these before the tool calls reach the rest of the system — otherwise
the LLM sees its own unparsed tool calls in the history and learns to
reproduce them.

Public interface:
    * ``extract_xml_tool_calls(content)`` — main entry point
    * ``detect_unparsed_tool_calls(content)`` — detection / warning-only
"""

from __future__ import annotations

import re
import secrets
import string
from typing import Any

from nanobot.providers.base import ToolCallRequest

_ALNUM = string.ascii_letters + string.digits

# Regex patterns to detect unparsed tool calls in LLM content (XML/ReAct, etc.)
_UNPARSED_TOOL_PATTERNS = [
    r"<invoke\s+name\s*=\s*[\"']([^\"']+)[\"']\s*>",
    r"<invoke\s+tool\s*=\s*[\"']([^\"']+)[\"']\s*>",     # MiniMax: <invoke tool="name">
    r"\{tool\s*=>\s*[\"']([^\"']+)[\"']\s*>",              # MiniMax: {tool => "name">
    r"\[TOOL_CALL\]",                                       # iOS agent: [TOOL_CALL] wrapper
    r"<tool>([^<]+)</tool>",
    r"Action\s*:\s*(\w+)",
]
_UNPARSED_TOOL_RE = re.compile("|".join(f"(?:{p})" for p in _UNPARSED_TOOL_PATTERNS))

# Safe regex for retry — only matches explicit XML tool calls that won't
# cause false positives in normal conversation.
# Covers both MiniMax formats: <invoke name="..."> and {tool => "..."}>.
_SAFE_INVOKE_RE = re.compile(
    r"<invoke\s+name\s*=\s*[\"']([^\"']+)[\"']\s*>"
    r"|<invoke\s+tool\s*=\s*[\"']([^\"']+)[\"']\s*>"
    r"|\{tool\s*=>\s*[\"']([^\"']+)[\"']\s*>"
    r"|\[TOOL_CALL\]"
)

# Strips wrapping <minimax:tool_call> / </minimax:tool_call> from cleaned content.
_MINIMAX_WRAPPER_RE = re.compile(r"</?minimax:[^>]*>")

# Matches MiniMax dict-format tool calls: {tool => "name", args => { --key "value" }}
_TOOL_CALL_OBJ_RE = re.compile(
    r"\{tool\s*=>\s*\"([^\"]+)\",\s*args\s*=>\s*\{"
)

# Matches standalone {tool name="name" args="--key "val" ..."} format (no [TOOL_CALL] wrapper).
_TOOL_CALL_ARGS_RE = re.compile(
    r"\{tool\s+name\s*=\s*\"([^\"]+)\"\s+args\s*=\s*\""
)

# Matches closing of a [TOOL_CALL] block — either the standard [/TOOL_CALL]
# or MiniMax </minimax:tool_call> (the API sometimes omits [/TOOL_CALL]).
_TC_CLOSE_RE = re.compile(r"\[/TOOL_CALL\]|</minimax:tool_call>")

# Matches any tool call start inside a [TOOL_CALL] block body.
_TOOL_INNER_RE = re.compile(
    r"\{tool\s+name\s*=\s*\"([^\"]+)\""            # {tool name="..." (format A)
    r"|\{tool\s*=>\s*\"([^\"]+)\""                 # {tool => "..." (formats B/C)
    r"|<invoke\s+(?:name|tool)\s*=\s*[\"']([^\"']+)[\"']\s*>"  # <invoke name/tool="..."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_tool_id() -> str:
    """9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _find_flag_close_quote(text: str, search_start: int) -> int:
    """Find the ``"`` that closes a flag value (handles embedded quotes).

    The ``"`` that ends the value is the first ``"`` at or after
    ``search_start`` that is followed by `` --``, ``}``, or end of string.
    """
    search = search_start
    while True:
        q = text.find('"', search)
        if q == -1:
            return len(text)
        rest = text[q + 1:].strip()
        if not rest or rest.startswith("--") or rest.startswith("}"):
            return q
        search = q + 1


def _parse_flag_pairs(text: str) -> dict[str, str]:
    """Parse ``--key "value"`` pairs, handling values that contain ``"``.

    The closing quote of each value is identified as the ``"`` immediately
    followed by `` --``, ``}``, or end-of-string — this correctly handles
    embedded quotes.  Skips any non-flag prefix (e.g. ``args=...``).
    """
    result: dict[str, str] = {}
    pos = 0
    while pos < len(text):
        m = re.match(r"--(\w+)\s+", text[pos:])
        if not m:
            pos += 1
            continue
        key = m.group(1)
        pos += m.end()
        if pos < len(text) and text[pos] == '"':
            val_start = pos + 1
            close = _find_flag_close_quote(text, val_start)
            result[key] = text[val_start:close]
            pos = close + 1
        else:
            end = re.search(r"\s+--", text[pos:])
            if end:
                result[key] = text[pos:pos + end.start()].strip()
                pos += end.start()
            else:
                result[key] = text[pos:].strip()
                break
    return result


def _add_tool_from_args(tool_name: str, body: str, result: list[ToolCallRequest]) -> None:
    """Parse ``args="--key "val" ..."`` body and append a ToolCallRequest."""
    args = _parse_flag_pairs(body)
    result.append(ToolCallRequest(id=_short_tool_id(), name=tool_name, arguments=args))


# ---------------------------------------------------------------------------
# Block parsers
# ---------------------------------------------------------------------------


def _find_tc_span(content: str, pos: int) -> tuple[int, int] | None:
    """Find the next ``[TOOL_CALL]`` block returning ``(inner_start, block_end)``.

    The block end is the first of: ``[/TOOL_CALL]``, ``</minimax:tool_call>``,
    the next ``[TOOL_CALL]``, or EOF.  Returns ``None`` if ``[TOOL_CALL]``
    does not appear at or after ``pos``.
    """
    open_m = re.search(r"\[TOOL_CALL\]", content[pos:])
    if not open_m:
        return None
    open_abs = pos + open_m.start()
    inner_start = open_abs + len("[TOOL_CALL]")

    close_m = _TC_CLOSE_RE.search(content, inner_start)
    next_open_m = re.search(r"\[TOOL_CALL\]", content[open_abs + 1:])

    boundaries = []
    if close_m:
        boundaries.append(close_m.start())
    if next_open_m:
        boundaries.append(open_abs + 1 + next_open_m.start())
    if not boundaries:
        return (inner_start, len(content))

    return (inner_start, min(boundaries))


def _parse_invoke_block(
    content: str,
    m: re.Match,
    param_re: re.Pattern,
    result: list[ToolCallRequest],
) -> int | None:
    """Parse ``<invoke>`` / ``{tool =>}`` XML block with ``<parameter>`` tags.

    Returns the position *after* the ``</invoke>`` closing tag, or ``None``
    if the closing tag cannot be found (caller should fall back to plain text).
    """
    tool_name = next((g for g in m.groups() if g is not None), None)
    if not tool_name:
        return None

    close = content.find("</invoke>", m.end())
    if close == -1:
        return None

    inner = content[m.end():close]
    args = dict(param_re.findall(inner))
    result.append(ToolCallRequest(
        id=_short_tool_id(),
        name=tool_name,
        arguments=args,
    ))
    return close + len("</invoke>")


def _parse_tc_multi(
    inner: str,
    param_re: re.Pattern,
    result: list[ToolCallRequest],
) -> None:
    """Parse all tool calls inside a ``[TOOL_CALL]`` block body.

    Handles multiple tool calls sequentially within one block.
    """
    spos = 0
    while spos < len(inner):
        m = _TOOL_INNER_RE.search(inner, spos)
        if not m:
            break

        tool_name = m.group(1) or m.group(2) or m.group(3)

        if m.group(1):
            # Format A: {tool name="..." args="--key "val" ..."}
            close = inner.find("}", m.end())
            if close == -1:
                spos = m.end()
                continue
            _add_tool_from_args(tool_name, inner[m.end():close], result)
            spos = close + 1

        elif m.group(3):
            # <invoke name/tool="..."> delegate to XML parser
            end_pos = _parse_invoke_block(inner, m, param_re, result)
            spos = m.end() if end_pos is None else end_pos

        else:
            # {tool => "..."} could be XML (format B) or dict (format C)
            after = inner[m.end():].lstrip()
            if after.startswith(">"):
                end_pos = _parse_invoke_block(inner, m, param_re, result)
                spos = m.end() if end_pos is None else end_pos
            elif after.startswith(","):
                # Format C: {tool => "name", args => { --key "val" }}
                close = inner.find("}}", m.end())
                if close == -1:
                    spos = m.end()
                    continue
                _add_tool_from_args(tool_name, inner[m.end():close], result)
                spos = close + 2
            else:
                spos = m.end()


def _parse_dict_block(
    content: str,
    m: re.Match,
    result: list[ToolCallRequest],
) -> int | None:
    """Parse ``{tool => "name", args => { --key "value" }}`` dict format.

    Returns the position after the closing ``}}``, or None if unparseable.
    """
    tool_name = m.group(1)
    close_pos = content.find("}}", m.end())
    if close_pos == -1:
        return None
    _add_tool_from_args(tool_name, content[m.end():close_pos], result)
    return close_pos + 2


def _parse_tool_call_args_block(
    content: str,
    m: re.Match,
    result: list[ToolCallRequest],
) -> int | None:
    """Parse ``{tool name="TOOL" args="--key "val" ..."}`` format (no [TOOL_CALL]).

    Strips the trailing ``"`` that closes the ``args="`` wrapper before
    delegating to ``_parse_flag_pairs``, which handles embedded quotes via
    ``_find_flag_close_quote``.

    Returns the position after the closing ``}``, or None if unparseable.
    """
    tool_name = m.group(1)
    end_brace = content.find("}", m.end())
    if end_brace == -1:
        return None
    raw = content[m.end():end_brace]
    # Strip the trailing " that closes args=""
    if raw.endswith('"'):
        raw = raw[:-1]
    _add_tool_from_args(tool_name, raw, result)
    return end_brace + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_unparsed_tool_calls(content: str | None) -> bool:
    """Return True if *content* contains unparsed tool call patterns.

    Logs a warning with the matched tool name and pattern type.  The actual
    extraction (removal from text) is done by ``extract_xml_tool_calls``.
    """
    if not content:
        return False
    m = _UNPARSED_TOOL_RE.search(content)
    if not m:
        return False
    from loguru import logger
    for i, g in enumerate(m.groups()):
        if g:
            logger.warning(
                "LLM content contains unparsed tool call '{}' (pattern: {}). "
                "The API did not return a structured tool_call — treating as plain text.",
                g, _UNPARSED_TOOL_PATTERNS[i].split("(")[0].strip("\\"),
            )
            return True
    return False


def extract_xml_tool_calls(content: str) -> tuple[list[ToolCallRequest], str | None]:
    """Extract tool calls from XML/content when the API didn't return structured ``tool_calls``.

    Handles these formats — both standalone and inside ``[TOOL_CALL]`` wrappers:
    * ``{tool name="..." args="--key "val" ..."}`` (iOS agent protocol)
    * ``{tool => "name">...<parameter>...</invoke>`` (MiniMax XML)
    * ``<invoke name="tool">...<parameter>...</invoke>`` (MiniMax XML)
    * ``{tool => "name", args => { --key "value" }}`` (MiniMax dict format)

    ``[TOOL_CALL]`` blocks may close with ``[/TOOL_CALL]`` or
    ``</minimax:tool_call>``.  Multiple tool calls within a single
    ``[TOOL_CALL]`` block are handled.

    Returns ``(tool_calls, cleaned_content)`` where ``cleaned_content`` is
    the original content with the XML blocks removed (or ``None`` if
    everything was consumed).
    """
    if not content:
        return [], content

    result: list[ToolCallRequest] = []
    cleaned: list[str] = []
    pos = 0
    param_re = re.compile(r"<parameter\s+name\s*=\s*\"([^\"]+)\"\s*>([^<]*)</parameter>")

    while pos < len(content):
        invoke_m = _SAFE_INVOKE_RE.search(content, pos)
        dict_m = _TOOL_CALL_OBJ_RE.search(content, pos)
        args_m = _TOOL_CALL_ARGS_RE.search(content, pos)

        if not invoke_m and not dict_m and not args_m:
            cleaned.append(content[pos:])
            break

        tc_span = None
        if invoke_m and invoke_m.group() == "[TOOL_CALL]":
            tc_span = _find_tc_span(content, invoke_m.start())

        candidates: list[tuple[int, str]] = []
        if tc_span:
            candidates.append((tc_span[0], "tc"))
        if invoke_m:
            candidates.append((invoke_m.start(), "invoke"))
        if dict_m:
            candidates.append((dict_m.start(), "dict"))
        if args_m:
            candidates.append((args_m.start(), "args"))
        candidates.sort(key=lambda x: x[0])

        kind = candidates[0][1]
        start_pos = candidates[0][0]
        cleaned.append(content[pos:start_pos])

        if kind == "tc":
            block_start, block_end = tc_span
            _parse_tc_multi(content[block_start:block_end], param_re, result)
            pos = block_end
        elif kind == "dict":
            end_pos = _parse_dict_block(content, dict_m, result)
            if end_pos is None:
                cleaned.append(dict_m.group())
                pos = dict_m.end()
                continue
            pos = end_pos
        elif kind == "args":
            end_pos = _parse_tool_call_args_block(content, args_m, result)
            if end_pos is None:
                cleaned.append(args_m.group())
                pos = args_m.end()
                continue
            pos = end_pos
        else:
            end_pos = _parse_invoke_block(content, invoke_m, param_re, result)
            if end_pos is None:
                cleaned.append(invoke_m.group())
                pos = invoke_m.end()
                continue
            pos = end_pos

    cleaned_text = _MINIMAX_WRAPPER_RE.sub("", "".join(cleaned)).strip()
    return result, cleaned_text or None
