import os

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage
from nanobot.config.paths import get_workspace_path


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





@pytest.mark.asyncio
async def test_message_tool_passes_through_url_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    url = "https://example.com/image.png"

    await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=[url],
    )

    assert sent[0].media == [url]


@pytest.mark.asyncio
async def test_message_tool_defer_mode_queues_and_explains_semantics() -> None:
    """When defer_mode is active, message() must NOT send and must return a
    placeholder that clearly tells the LLM the message is queued, NOT delivered.

    Regression guard for: cron agent was confused by 'queued for delivery' status
    and didn't know whether the message was sent.
    """
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_defer_mode(True)

    result = await tool.execute(
        content="hello user",
        channel="telegram",
        chat_id="1",
    )

    # Nothing was actually sent — caller (framework) decides when to flush
    assert sent == []
    # The placeholder must contain the key clarifications so the LLM doesn't
    # assume the message was delivered.
    assert "QUEUED" in result or "queued" in result
    assert "NOT yet sent" in result or "NOT" in result
    assert "DISCARDED" in result or "discarded" in result
    # The placeholder must mention the assess mechanism so the LLM understands
    # the queue/assess cycle.
    assert "assess" in result.lower() or "quality" in result.lower()
    # Deferred list should hold exactly one message for later flush
    assert tool.has_deferred is True
    assert len(tool._deferred) == 1

    # flush_deferred() should actually deliver
    await tool.flush_deferred()
    assert len(sent) == 1
    assert sent[0].content == "hello user"
    assert tool.has_deferred is False


@pytest.mark.asyncio
async def test_message_tool_defer_mode_replacement_after_revisions() -> None:
    """Calling message() multiple times under defer_mode should queue each one
    (the most recent represents the user's intent). The framework's
    _clear_msg_deferred() is what discards — flush_deferred() delivers.
    """
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_defer_mode(True)

    r1 = await tool.execute(content="first draft", channel="feishu", chat_id="x")
    r2 = await tool.execute(content="revised", channel="feishu", chat_id="x")
    r3 = await tool.execute(content="final", channel="feishu", chat_id="x")

    # All three should report queued (not error)
    assert "queued" in r1.lower()
    assert "queued" in r2.lower()
    assert "queued" in r3.lower()
    # Nothing delivered yet
    assert sent == []
    # Three messages in the deferred list
    assert len(tool._deferred) == 3

    # clear_deferred() should drop them all
    tool.clear_deferred()
    assert tool.has_deferred is False
    # No flush happened, so nothing reached the user
    assert sent == []


