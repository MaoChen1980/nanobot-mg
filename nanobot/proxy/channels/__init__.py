"""Proxy channels - each runs as a separate process.

Lazy imports via ``__getattr__`` so that channel modules used as
``__main__`` entry points (e.g. ``python -m nanobot.proxy.channels.feishu``)
are imported only when actually accessed, avoiding a CPython RuntimeWarning
about finding the module in ``sys.modules`` before the package init executes.
"""

from __future__ import annotations

import importlib
import typing

if typing.TYPE_CHECKING:
    from nanobot.proxy.channels.dingtalk import DingTalkProxyChannel
    from nanobot.proxy.channels.discord import DiscordProxyChannel
    from nanobot.proxy.channels.email import EmailProxyChannel
    from nanobot.proxy.channels.feishu import FeishuProxyChannel
    from nanobot.proxy.channels.qq import QQProxyChannel
    from nanobot.proxy.channels.slack import SlackProxyChannel
    from nanobot.proxy.channels.telegram import TelegramProxyChannel
    from nanobot.proxy.channels.weixin import WeixinProxyChannel
    from nanobot.proxy.channels.whatsapp import WhatsAppProxyChannel

_CHANNEL_MODULES: dict[str, str] = {
    "DingTalkProxyChannel": "nanobot.proxy.channels.dingtalk",
    "DiscordProxyChannel": "nanobot.proxy.channels.discord",
    "EmailProxyChannel": "nanobot.proxy.channels.email",
    "FeishuProxyChannel": "nanobot.proxy.channels.feishu",
    "QQProxyChannel": "nanobot.proxy.channels.qq",
    "SlackProxyChannel": "nanobot.proxy.channels.slack",
    "TelegramProxyChannel": "nanobot.proxy.channels.telegram",
    "WeixinProxyChannel": "nanobot.proxy.channels.weixin",
    "WhatsAppProxyChannel": "nanobot.proxy.channels.whatsapp",
}


def __getattr__(name: str) -> typing.Any:
    if name in _CHANNEL_MODULES:
        module = importlib.import_module(_CHANNEL_MODULES[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_CHANNEL_MODULES.keys())