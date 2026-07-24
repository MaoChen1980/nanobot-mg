import os

import pytest

from nanobot.agent.tools.message import MessageTool, strip_framework_markers
from nanobot.bus.events import OutboundMessage
from nanobot.config.paths import get_workspace_path


@pytest.mark.parametrize(
    "raw, expected",
    [
        # [assess]...[/assess] block stripped
        (
            "Here is the result.\n[assess]\nSome calibration text.\n[/assess]\nDone.",
            "Here is the result.\nDone.",
        ),
        # [debug_root_cause] block stripped
        (
            "[debug_root_cause]Root cause analysis[/debug_root_cause]\nUser sees this.",
            "User sees this.",
        ),
        # truncated chars placeholder stripped
        (
            "First line\n(truncated, 1234 chars)\nSecond line",
            "First line\nSecond line",
        ),
        # [...] truncated marker stripped (3 dots variant)
        (
            "Start [...100 characters truncated] End",
            "Start  End",
        ),
        # <!-- no-assess --> stripped (consecutive newlines collapsed)
        (
            "Text\n<!-- no-assess -->\nmore text",
            "Text\nmore text",
        ),
        # [assess_me] stripped (leaves spaces/words on either side untouched)
        (
            "Content [assess_me] extra",
            "Content  extra",
        ),
        # case-insensitive
        (
            "[ASSESS]inner[/ASSESS] clean",
            "clean",
        ),
        # no markers — unchanged
        ("Just user content.", "Just user content."),
        ("", ""),
    ],
)
def test_strip_framework_markers(raw: str, expected: str) -> None:
    assert strip_framework_markers(raw) == expected


@pytest.mark.asyncio
async def test_message_tool_strips_framework_markers() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    dirty = "Real data.\n[assess]calibration[/assess]\nMore data."
    await tool.execute(content=dirty, channel="feishu", chat_id="chat1")
    assert sent[0].content == "Real data.\nMore data."


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not a list",
        [["ok"], "row-not-a-list"],
        [["ok", 42]],
        [[None]],
    ],
)
async def test_message_tool_rejects_malformed_buttons(bad) -> None:
    """``buttons`` must be ``list[list[str]]``; the tool validates the shape
    up front so a malformed LLM payload errors visibly instead of slipping
    into the channel layer where Telegram would silently reject the frame."""
    tool = MessageTool()
    result = await tool.execute(
        content="hi", channel="telegram", chat_id="1", buttons=bad,
    )
    assert result == "Error: buttons must be a list of list of strings"


@pytest.mark.asyncio
async def test_message_tool_marks_channel_delivery_only_when_enabled() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    await tool.execute(content="normal", channel="telegram", chat_id="1")
    token = tool.set_record_channel_delivery(True)
    try:
        await tool.execute(content="cron", channel="telegram", chat_id="1")
    finally:
        tool.reset_record_channel_delivery(token)

    assert sent[0].metadata == {}
    assert sent[1].metadata == {"_record_channel_delivery": True}


@pytest.mark.asyncio
async def test_message_tool_inherits_metadata_for_same_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    slack_meta = {"slack": {"thread_ts": "111.222", "channel_type": "channel"}}
    tool.set_context("slack", "C123", metadata=slack_meta)

    await tool.execute(content="thread reply")

    assert sent[0].metadata == slack_meta


@pytest.mark.asyncio
async def test_message_tool_does_not_inherit_metadata_for_cross_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_context(
        "slack",
        "C123",
        metadata={"slack": {"thread_ts": "111.222", "channel_type": "channel"}},
    )

    await tool.execute(content="channel reply", channel="slack", chat_id="C999")

    assert sent[0].metadata == {}

