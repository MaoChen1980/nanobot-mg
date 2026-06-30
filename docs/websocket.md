# Hub TCP 协议

NanoBot 的 proxy 子进程（飞书、钉钉、Telegram 等渠道）通过 TCP 连接到 gateway 内部的 Hub 服务器进行通信。这是一种基于 JSON Lines 的简单协议。

---

## 架构

```
Proxy 进程 (渠道)              Gateway 进程
                       ┌──────────────────────┐
  Feishu Proxy ──TCP──▶│   HubTCPServer       │──▶ AgentLoop
  DingTalk Proxy ──TCP─▶│   (port: 18791)      │
  Telegram Proxy ──TCP──▶│   + ProxyManager     │──▶ Message Bus
                       └──────────────────────┘
```

- Hub 端口固定为 gateway 端口 + 1（默认 `18791`）
- 每个 proxy 进程维持一条长连接
- 连接断开即视为 proxy 进程死亡

## 连接流程

```
Proxy                          Hub (TCP Server)
  │                               │
  │── TCP connect (18791) ────────▶│
  │                               │
  │── {"type":"register",...} ────▶│  注册身份
  │◀── {"success":true,...} ──────│  注册确认
  │                               │
  │── ProxyMessage (JSON, 无type) ─▶  发送消息
  │◀── HubResponse (JSON, 无type) ─│  处理结果
  │                               │
  │◀── {"type":"deliver",...} ────│  主动推送（Hub → Proxy）
```

## 消息格式

所有消息为**一行 JSON**（JSON Lines），以 `\n` 结尾。

### 注册消息（Proxy → Hub）

```json
{
  "type": "register",
  "channel": "feishu",
  "bot": "nanobot",
  "pid": 12345
}
```

**注册响应（Hub → Proxy）：**

```json
{
  "success": true
}
```

### ProxyMessage（Proxy → Hub，隐式类型）

向 Hub 发送用户消息。无 `type` 字段，包含完整的消息数据：

```json
{
  "channel": "feishu",
  "bot": "nanobot",
  "sender_id": "ou_xxxxx",
  "chat_id": "oc_xxxxx",
  "content": "你好",
  "message_id": "om_xxxxx",
  "media": [],
  "timestamp": "2026-06-30T12:00:00+08:00",
  "metadata": {}
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `channel` | string | 是 | 渠道名称，如 `feishu`、`dingtalk`、`telegram` |
| `bot` | string | 是 | 机器人名称，对应配置中的 key |
| `sender_id` | string | 是 | 发送者用户标识 |
| `chat_id` | string | 是 | 聊天/群组标识 |
| `content` | string | 是 | 消息文本内容 |
| `message_id` | string | 是 | 平台消息 ID，用于去重和回复追溯 |
| `media` | string[] | 否 | 媒体文件 URL 列表 |
| `timestamp` | string | 否 | ISO 8601 时间戳 |
| `metadata` | object | 否 | 渠道特定的扩展数据 |

### HubResponse（Hub → Proxy，隐式类型）

对 ProxyMessage 的处理结果回复。无 `type` 字段：

```json
{
  "success": true,
  "reply_to": "om_xxxxx",
  "content": "这是回复内容",
  "media": [],
  "metadata": {},
  "error": "",
  "buttons": [["确认", "取消"]]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 处理是否成功 |
| `reply_to` | string | 回复的目标消息 ID |
| `content` | string | 回复文本 |
| `media` | string[] | 媒体文件路径列表 |
| `metadata` | object | 扩展数据 |
| `error` | string | 错误信息（失败时） |
| `buttons` | string[][] | 按钮数组（每行一个按钮组） |

### deliver 推送（Hub → Proxy）

当 Agent 需要主动向聊天推送消息（如工具调用通知、思考过程、定时任务触发）时，Hub 通过此消息推送给对应的 proxy：

```json
{
  "type": "deliver",
  "chat_id": "oc_xxxxx",
  "content": "主动推送的消息",
  "reply_to": "om_xxxxx",
  "media": ["/path/to/file.jpg"],
  "buttons": [["确认"]]
}
```

## 消息类型汇总

| 类型 | 方向 | 说明 |
|------|------|------|
| `register` | Proxy → Hub | 注册连接，标识渠道和机器人身份 |
| `deliver` | Hub → Proxy | 主动推送消息到聊天 |
| ProxyMessage | Proxy → Hub | 用户消息（无 `type` 字段，通过 JSON 字段隐式识别） |
| HubResponse | Hub → Proxy | 消息处理结果（无 `type` 字段） |

## 去重机制

Hub 内部维护消息去重缓存（TTL 300 秒），依据 `message_id` 进行去重。重复的消息会被忽略，避免 Agent 重复处理。

## 常见问题

### Q: Proxy 连接不上 Hub

确保 gateway 已启动，且 Hub 端口未被占用。Hub 端口 = gateway 端口 + 1。

### Q: 消息格式错误

确保：
- 每条消息是合法的 JSON
- 以 `\n` 结尾（JSON Lines 格式）
- 字段名和类型与协议定义一致

### Q: 如何发送带按钮的消息

在 HubResponse 或 deliver 消息中设置 `buttons` 字段：

```json
{
  "type": "deliver",
  "chat_id": "xxx",
  "content": "请选择一项操作：",
  "buttons": [["选项A", "选项B"], ["取消"]]
}
```
