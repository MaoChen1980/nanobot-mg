"""Observe toggles: /think, /tool, /debug."""

from __future__ import annotations

from nanobot.bus.events import OutboundMessage


def _observe_state(ctx, flag: str, default: bool = True) -> str:
    """Get a human-readable observe state string."""
    enabled = ctx.loop._session_observe[flag].get(ctx.key, default)
    return f"🟢 **ON**" if enabled else f"🔴 **OFF**"


def register_observe_commands(router) -> None:
    router.exact("/think", cmd_think)
    router.exact("/think status", cmd_think_status)
    router.exact("/tool", cmd_tool)
    router.exact("/tool status", cmd_tool_status)
    router.exact("/debug", cmd_debug)


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


async def cmd_think_status(ctx) -> OutboundMessage:
    """Show current /think state without toggling."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=f"🧠 /think is {_observe_state(ctx, '_observe_think')}",
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


async def cmd_tool_status(ctx) -> OutboundMessage:
    """Show current /tool state without toggling."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=f"🔧 /tool is {_observe_state(ctx, '_observe_tool')}",
        metadata=dict(ctx.msg.metadata or {}),
    )


async def cmd_debug(ctx) -> OutboundMessage:
    """Toggle raw prompt dumping to ~/.nanobot/debug/."""
    session_key = ctx.key
    enabled = ctx.loop._session_observe["_observe_debug"].get(session_key, False)
    enabled = not enabled
    ctx.loop._session_observe["_observe_debug"][session_key] = enabled
    status = "ON" if enabled else "OFF"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=(
            f"🔍 /debug {status} — Raw prompts will{' ' if enabled else ' not '}be saved to ~/.nanobot/debug/."
        ),
        metadata=dict(ctx.msg.metadata or {}),
    )