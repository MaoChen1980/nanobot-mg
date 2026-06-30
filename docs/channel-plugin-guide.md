# 消息通道插件开发指南

NanoBot 的消息通道（Channel）采用独立进程 + TCP 连接的架构。

## 内置通道列表

NanoBot 内置以下消息通道，位于 [nanobot/proxy/channels/](../nanobot/proxy/channels/)：

| 通道 | 文件 | 说明 |
|------|------|------|
| **飞书** | [feishu.py](../nanobot/proxy/channels/feishu.py) | 连接飞书 WebSocket 服务，支持消息和事件 |
| **钉钉** | [dingtalk.py](../nanobot/proxy/channels/dingtalk.py) | 基于钉钉 SDK，支持消息收发和事件处理 |
| **Telegram** | [telegram.py](../nanobot/proxy/channels/telegram.py) | 基于 python-telegram-bot，支持消息、命令、媒体 |
| **Discord** | [discord.py](../nanobot/proxy/channels/discord.py) | 基于 discord.py，支持消息和 slash 命令 |
| **Slack** | [slack.py](../nanobot/proxy/channels/slack.py) | 基于 Slack Socket Mode，实时消息 |
| **WhatsApp** | [whatsapp.py](../nanobot/proxy/channels/whatsapp.py) | 基于 neonize，支持消息和媒体 |
| **个人微信** | [weixin.py](../nanobot/proxy/channels/weixin.py) | 通过 HTTP API 接入，无需客户端登录 |
| **QQ** | [qq.py](../nanobot/proxy/channels/qq.py) | 基于 Tencent Botpy SDK，支持频道和私聊 |
| **电子邮件** | [email.py](../nanobot/proxy/channels/email.py) | IMAP 收信 + SMTP 发信，支持纯文本和 HTML |

各通道的配置方式见 [配置参考](configuration.md) 的 channels 一节。

---

## 架构

```
┌─────────────────────────────────────┐
│             Hub (Gateway)            │
│  HubTCPServer + ProxyManager        │
│  TCP 端口监听                       │
└──────────┬──────────────────────────┘
           │ TCP JSON Lines
           │
┌──────────▼──────────────────────────┐
│      Proxy 进程 (独立子进程)          │
│  ┌──────────────────────────────┐   │
│  │ BaseProxyChannel（基类）      │   │
│  │ ├─ TCP 连接管理               │   │
│  │ ├─ 消息去重                   │   │
│  │ ├─ 父进程监控                 │   │
│  │ └─ 配置热重载                 │   │
│  │                              │   │
│  │ ┌──────────────────────────┐ │   │
│  │ │ 具体通道实现              │ │   │
│  │ │ (Feishu/Telegram/QQ 等)   │ │   │
│  │ └──────────────────────────┘ │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

---

## BaseProxyChannel 基类

位于 [nanobot/proxy/channels/base.py](../nanobot/proxy/channels/base.py)。

### 必须实现的类属性

```python
class MyChannel(BaseProxyChannel):
    CHANNEL_NAME = "MyChannel"          # 人类可读名称
    REQUIRED_CONFIG_FIELDS = ["api_key", "secret"]  # 必需的配置字段
```

### 必须实现的方法

```python
def start(self) -> None:
    """进入通道自身的消息监听循环。
    
    阻塞方式运行，通常包含一个 while True 循环来轮询或监听消息。
    收到消息后通过 self.send_to_hub() 转发给 Hub。
    """

def send_reply(self, chat_id: str, reply_to: str, content: str) -> None:
    """发送文本回复到通道。
    
    当 Hub 返回回复时，通过 FIFO 队列调用此方法。
    chat_id: 聊天/群组 ID
    reply_to: 原始消息 ID（用于回复线程）
    content: 要发送的文本
    """
```

### 可选重写的方法

```python
async def _handle_deliver(self, data: dict[str, Any]) -> None:
    """处理 Hub 推送的消息。
    
    默认实现记录日志。重写此方法来处理进度更新、
    工具事件推送等。data 包含:
    - chat_id: 目标聊天 ID
    - content: 推送内容（可能是进度、工具事件或最终回复）
    - media: 媒体文件列表
    """

def _process_send(self, item: dict) -> None:
    """处理 FIFO 发送队列中的项目。
    
    默认抛出 NotImplementedError。重写以实现具体的发送逻辑。
    运行在发送工作线程上。
    """

async def _send_startup_notification(self) -> None:
    """发送启动通知到最近活跃的聊天。
    
    默认不做任何操作（base stub）。重写以在启动时通知用户。
    """
```

### 基类提供的功能

| 方法 | 说明 |
|------|------|
| `connect_to_hub()` | 连接到 Hub TCP 服务器并注册 |
| `send_to_hub(msg_data)` | 向 Hub 转发消息（线程安全） |
| `async_send_to_hub(msg_data)` | 异步版 send_to_hub |
| `check_duplicate(msg_id, ttl=300)` | 消息去重检查 |
| `build_message(sender_id, chat_id, content, ...)` | 构建标准消息字典 |
| `validate_config(config)` | 验证配置是否包含必需字段 |
| `run_main()` | 标准入口点 |
| `_save_media_bytes(filename, data)` | 保存媒体文件到工作区 |
| `_scan_media_paths(content)` | 扫描内容中的媒体文件引用 |

---

## Hub 连接协议（TCP JSON Lines）

### 注册

```
Proxy → Hub:
{"type":"register", "channel":"mychannel", "bot":"nanobot", "pid":12345}

Hub → Proxy:
{"success":true}
```

### 消息发送

```
Proxy → Hub:
{
  "type": "message",
  "channel": "mychannel",
  "bot": "nanobot",
  "sender_id": "user_001",
  "chat_id": "chat_001",
  "content": "用户消息内容",
  "message_id": "msg_001",
  "media": ["/path/to/file.jpg"],
  "timestamp": "2026-06-30T12:00:00Z"
}
```

### 回复接收

Hub 通过 `type: "deliver"` 推送消息回代理，包括进度更新和最终回复：

```
Hub → Proxy:
{
  "type": "deliver",
  "chat_id": "chat_001",
  "content": "回复内容",
  "success": true,
  "reply_to": "msg_001",
  "media": [],
  "buttons": [["按钮1", "按钮2"]]
}
```

### ProxyMessage 协议

位于 [nanobot/proxy/protocol.py](../nanobot/proxy/protocol.py)：

```python
@dataclass
class ProxyMessage:
    channel: str          # 通道名
    bot: str              # Bot 名
    sender_id: str        # 发送者 ID
    chat_id: str          # 聊天 ID
    content: str          # 消息内容
    message_id: str       # 平台消息 ID
    media: list[str]      # 媒体文件路径列表
    timestamp: str        # ISO 格式时间戳
    metadata: dict        # 扩展元数据

@dataclass
class HubResponse:
    success: bool
    reply_to: str         # 回复的目标消息 ID
    content: str          # 回复内容
    media: list[str]
    metadata: dict
    error: str
    buttons: list[list[str]]
```

---

## 通道注册与发现

NanoBot 使用 `pkgutil.iter_modules` 自动发现 `nanobot.proxy.channels` 包下的所有模块。

位于 [nanobot/proxy/registry.py](../nanobot/proxy/registry.py)：

```python
def discover_channel_names() -> list[str]:
    """扫描 nanobot.proxy.channels 包，返回所有模块名"""
    import nanobot.proxy.channels as pkg
    return [
        name
        for _, name, ispkg in pkgutil.iter_modules(pkg.__path__)
        if name not in _INTERNAL and not ispkg
    ]

def get_channel_info(name: str) -> dict | None:
    """加载通道模块，提取显示名和配置类"""

def discover_all() -> dict[str, dict]:
    """返回 {通道名: info_dict} 的所有通道"""
```

### 通道模块的自动发现规则

1. 模块必须位于 `nanobot.proxy.channels` 包内
2. 模块名不能是内部模块（`__init__`）
3. 模块必须包含一个名为 `{Name}ProxyChannel` 的类（首字母大写）
4. 该类必须继承 `BaseProxyChannel`

---

## 创建自定义通道的完整步骤

### 1. 创建通道文件

在 `nanobot/proxy/channels/` 下创建新文件，如 `my_channel.py`：

```python
"""Custom message channel for MyPlatform."""

from __future__ import annotations

from nanobot.proxy.channels.base import BaseProxyChannel


class MyChannelProxyChannel(BaseProxyChannel):
    """MyPlatform message channel.
    
    类命名规则：模块名首字母大写 + "ProxyChannel"
    """

    CHANNEL_NAME = "MyChannel"
    REQUIRED_CONFIG_FIELDS = ["api_key"]

    def __init__(self, config, hub_tcp_host, hub_tcp_port, channel, bot):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        # 初始化通道特有的客户端
        self._client = None

    def start(self) -> None:
        """进入消息监听循环，阻塞运行。"""
        # 连接平台 API
        self._client = connect_to_platform(self.config["api_key"])
        
        # 通知 Hub 通道已就绪
        self.notify_ready()
        
        # 消息轮询循环
        while True:
            messages = self._client.poll_messages()
            for msg in messages:
                # 去重检查
                if self.check_duplicate(msg.id):
                    continue
                
                # 构建并发送
                data = self.build_message(
                    sender_id=msg.sender_id,
                    chat_id=msg.chat_id,
                    content=msg.text,
                    message_id=msg.id,
                    media=msg.media_files,
                )
                self.send_to_hub(data)
            
            time.sleep(1)

    def send_reply(self, chat_id: str, reply_to: str, content: str) -> None:
        """发送文本回复。"""
        self._client.send_message(chat_id, content, reply_to=reply_to)

    def _process_send(self, item: dict) -> None:
        """处理 FIFO 发送队列（用于媒体消息等复杂发送）。"""
        chat_id = item["chat_id"]
        content = item.get("content", "")
        media_paths = item.get("media", [])
        # 发送包含媒体的消息
        self._client.send_with_media(chat_id, content, media_paths)
```

### 2. 注册到 __init__.py

编辑 [nanobot/proxy/channels/__init__.py](../nanobot/proxy/channels/__init__.py)：

```python
_CHANNEL_MODULES: dict[str, str] = {
    # ... 现有通道 ...
    "MyChannelProxyChannel": "nanobot.proxy.channels.my_channel",
}

__all__ = list(_CHANNEL_MODULES.keys())
```

### 3. 配置 pyproject.toml

添加入口点（entry point）以便通过命令行启动：

```toml
[project.scripts]
nanobot-my-channel = "nanobot.proxy.channels.my_channel:MyChannelProxyChannel.run_main"
```

或直接在 `pyproject.toml` 中注册为 NanoBot 插件：

```toml
[project.entry-points."nanobot.channels"]
my_channel = "nanobot.proxy.channels.my_channel"
```

### 4. 配置 config.json

```json
{
  "channels": {
    "my_channel": {
      "enabled": true,
      "api_key": "your-api-key",
      "max_message_age": 300
    }
  }
}
```

---

## 完整示例：基于 WebSocket 的通道

```python
"""WebSocket channel example."""

from __future__ import annotations
import asyncio
import json
import time
from nanobot.proxy.channels.base import BaseProxyChannel


class WebsocketProxyChannel(BaseProxyChannel):
    """WebSocket-based message channel."""

    CHANNEL_NAME = "WebSocket"
    REQUIRED_CONFIG_FIELDS = ["ws_url"]

    def __init__(self, config, hub_tcp_host, hub_tcp_port, channel, bot):
        super().__init__(config, hub_tcp_host, hub_tcp_port, channel, bot)
        self._ws_url = config["ws_url"]
        self._ws = None

    def start(self) -> None:
        """使用 asyncio 事件循环监听 WebSocket 消息。"""
        asyncio.run(self._run())

    async def _run(self):
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self._ws_url) as ws:
                self._ws = ws
                self.notify_ready()
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        msg_data = self.build_message(
                            sender_id=data["from"],
                            chat_id=data["room"],
                            content=data["text"],
                            message_id=data.get("id", ""),
                        )
                        self.send_to_hub(msg_data)

    async def _handle_deliver(self, data: dict) -> None:
        """处理 Hub 的推送，包括进度更新和最终回复。"""
        chat_id = data.get("chat_id", "")
        content = data.get("content", "")
        if self._ws and content:
            await self._ws.send_json({
                "type": "reply",
                "to": chat_id,
                "content": content,
            })

    def send_reply(self, chat_id, reply_to, content):
        """通过 FIFO 队列发送文本回复。"""
        self._enqueue_send({
            "chat_id": chat_id,
            "content": content,
        })

    def _process_send(self, item: dict):
        """在工作线程上处理发送。"""
        import asyncio
        asyncio.run_coroutine_threadsafe(
            self._ws.send_json(item),
            self._conn_loop,
        ).result()
```

---

## 最佳实践

1. **消息去重**：始终使用 `check_duplicate(msg_id)` 防止重复处理
2. **媒体文件保存**：使用 `_save_media_bytes()` 保存上传文件到工作区
3. **配置验证**：在 `REQUIRED_CONFIG_FIELDS` 中声明必需字段
4. **启动通知**：准备好后调用 `notify_ready()` 通知用户
5. **线程安全**：`send_to_hub()` 是线程安全的，可在任意线程调用
6. **FIFO 队列**：使用 `_enqueue_send()` 和 `_process_send()` 确保消息发送顺序
7. **错误处理**：捕获平台 API 异常，记录日志，避免进程崩溃
