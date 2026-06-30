# 多实例运行

NanoBot 支持通过 Hub 架构运行多个实例，让不同的 Bot 实例处理不同的消息通道。每个通道作为一个独立进程（Proxy）运行，通过 TCP 连接到 Hub。

## 架构

```
                   ┌──────────────────┐
                   │   Hub (核心进程)    │
                   │  ┌──────────────┐ │
                   │  │  AgentLoop    │ │
                   │  │  LLM 处理     │ │
                   │  │  Hook 系统    │ │
                   │  └──────────────┘ │
                   └────────┬─────────┘
                            │ TCP JSON Lines
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │ Feishu Bot │   │ Telegram Bot│   │  QQ Bot    │
   │ (Proxy进程)│   │ (Proxy进程) │   │ (Proxy进程)│
   └────────────┘   └────────────┘   └────────────┘
```

### 核心组件

- **Hub**（[hub.py](../nanobot/proxy/hub.py)）— TCP 服务端，接收代理连接并路由消息到 AgentLoop
- **ProxyManager**（[manager.py](../nanobot/proxy/manager.py)）— 代理进程的生命周期管理器
- **Proxy 进程** — 每个通道作为一个单独的子进程运行

---

## ProxyManager：代理生命周期管理

[manager.py](../nanobot/proxy/manager.py) 的 `ProxyManager` 类负责：

### 数据结构

```python
class ProxyInfo:
    channel: str       # 通道名称，如 "feishu"
    bot: str           # Bot 名称，如 "nanobot"
    process: subprocess.Popen   # 代理子进程
    registration: dict          # 注册信息
    reader: asyncio.StreamReader | None  # TCP 读取流
    writer: asyncio.StreamWriter | None  # TCP 写入流
    running: bool
    last_heartbeat: float

class ProxyManager:
    _proxies: dict[str, ProxyInfo]  # key = "channel:bot"
    _writers: dict[int, str]        # writer_id → proxy key
```

### 核心功能

| 方法 | 说明 |
|------|------|
| `start_all()` | 根据配置启动所有代理进程 |
| `stop()` | 停止所有代理 |
| `stop_proxy(key)` | 停止指定代理 |
| `register_via_tcp(key, reader, writer, reg)` | TCP 连接注册 |
| `deliver_to_proxy(key, data)` | 向代理发送数据 |
| `get_proxy_keys()` | 获取所有代理键列表 |

### 启动流程

1. 读取配置文件，遍历所有启用的通道
2. 为每个通道创建 `asyncio.subprocess.Popen` 进程
3. 代理进程通过 TCP 连接到 Hub 并发送注册消息
4. ProxyManager 建立 TCP 读写流，开始转发消息

```python
# ProxyManager 启动代理子进程
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "nanobot", "proxy", channel, bot_name,
    "--hub-url", hub_url,
    "--hub-tcp-port", str(tcp_port),
    ...
)
```

---

## HubTCPServer：消息路由

[hub.py](../nanobot/proxy/hub.py) 的 `HubTCPServer` 类：

### 协议

- 基于 **TCP JSON Lines** 协议
- 代理发送 JSON 行到 Hub
- Hub 回复 JSON 行到代理
- 连接断开 = 代理死亡信号

### 消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| `register` | 代理 → Hub | 注册代理（channel, bot, pid）|
| `message` | 代理 → Hub | 用户消息（content, sender_id, media 等）|
| `deliver` | Hub → 代理 | 回复或进度更新（content, tool_events 等）|

### 注册流程

```
代理: {"type":"register", "channel":"feishu", "bot":"nanobot", "pid":12345}
Hub:  {"success":true}
```

### 消息路由

```
代理: {"type":"message", "channel":"feishu", "bot":"nanobot",
       "sender_id":"ou_xxx", "chat_id":"oc_xxx", "content":"你好"}
Hub:  {"type":"deliver", "chat_id":"oc_xxx", "content":"回复内容"}
```

Hub 处理消息的完整流程（`_route_message`）：

1. 反序列化为 `ProxyMessage`
2. 构建 session_key（`channel:bot:sender_id`）
3. 消息去重（message_id + DEDUP_TTL = 300s）
4. 会话锁序列化处理（同 session 的消息串行处理）
5. 支持 /stop 命令中断当前处理
6. 支持 mid-turn injection（会话忙时新消息排队等待当前轮次的中断点注入）
7. 调用 `AgentLoop.process_direct()` 处理消息
8. 通过 `ProxyManager.deliver_to_proxy()` 发送回复

### 中间轮次进度推送

使用 `_make_progress_callback` 构建进度回调，在 LLM 工具调用期间推送实时进度：

```python
_on_progress = self._make_progress_callback(self._proxy_manager, proxy_key, msg.chat_id)
# 进度消息格式：
{"type":"deliver", "chat_id":"oc_xxx", "content":"✅ 搜索工具 completed"}
```

---

## 配置方式

在 `config.json` 中配置多个通道：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "app_id": "...",
      "app_secret": "..."
    },
    "telegram": {
      "enabled": true,
      "bot_token": "..."
    },
    "qq": {
      "enabled": true,
      "qq_number": 123456789
    }
  }
}
```

### 启动命令

```bash
# 启动 Gateway（包含 Hub 和 AgentLoop）
nanobot gateway

# 或直接指定配置
nanobot --config /path/to/config.json gateway
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `NANOBOT_MAX_SUBAGENTS` | 每个实例的最大并发子代理数 | `5` |
| `NANOBOT_WORKSPACE` | 工作区路径 | `~/.nanobot/workspace` |
| `NANOBOT_CONFIG_PATH` | 配置文件路径 | 自动检测 |

---

## 注意事项

### 会话隔离

每个 session（由 `channel:bot:sender_id` 标识）有独立的锁和消息队列。不同用户的会话完全隔离，不会互相阻塞。

### 去重机制

Hub 维护一个 `_seen_message_ids` 字典（TTL 300s），防止代理重连导致的消息重复处理。当字典超过 10000 条时自动清理过期的条目。

### 并发控制

可选 `concurrency_gate`（`asyncio.Semaphore`）限制全局并发处理数。

### 代理进程健康检查

- **TCP keepalive**：Hub 和 Proxy 之间启用 TCP keepalive（30s 空闲检测周期）
- **父进程监控**：Proxy 定期（30s）检查父进程（Gateway）是否存活，若父进程死亡则自动退出
- **配置热重载**：Proxy 定期检查配置文件，如果通道被禁用则自动退出

### 端口冲突

Hub TCP 服务器在 Windows 上遇到 TIME_WAIT 端口冲突时会自动重试（最多 25 次，每次 3s 间隔）。

### 会话缓存

Hub 维护 session 缓存（上限 1000 条）。超过上限时自动淘汰最早的空队列 session，防止内存泄漏。
