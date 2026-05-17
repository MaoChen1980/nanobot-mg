"""Observe toggles: /think, /tool."""

from __future__ import annotations

from nanobot.bus.events import OutboundMessage


def register_observe_commands(router) -> None:
    router.exact("/think", cmd_think)
    router.exact("/tool", cmd_tool)


async def cmd_think(ctx) -> OutboundMessage:
    """Toggle LLM thinking/thinking block visibility in the channel."""
    session_key = ctx.key
    enabled = ctx.loop._session_observe["_observe_think"].get(session_key, True)
    enabled = not enabled
    ctx.loop._session_observe["_observe_think"][session_key] = enabled
    status = "ON" if enabled else "OFF"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=(
            f"🧠 /think {status} — LLM reasoning blocks will{' ' if enabled else ' not '}be shown."
        ),
        metadata=dict(ctx.msg.metadata or {}),
    )


async def cmd_tool(ctx) -> OutboundMessage:
    """Toggle tool call start/end events visibility in the channel."""
    session_key = ctx.key
    enabled = ctx.loop._session_observe["_observe_tool"].get(session_key, True)
    enabled = not enabled
    ctx.loop._session_observe["_observe_tool"][session_key] = enabled
    status = "ON" if enabled else "OFF"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=(
            f"🔧 /tool {status} — Tool call events will{' ' if enabled else ' not '}be shown."
        ),
        metadata=dict(ctx.msg.metadata or {}),
    )