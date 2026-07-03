# Chat Apps — 聊天应用

NanoBot 的聊天应用功能提供了在对话中使用的斜杠命令（slash commands）系统。用户可以在聊天输入框中输入以 `/` 开头的命令来控制代理行为、查看状态、管理会话等。

## 命令路由机制


命令路由由 `CommandRouter` 类实现。它采用纯字典驱动的四层分发机制：

### 四层匹配顺序

```
优先级 (priority) → 精确 (exact) → 前缀 (prefix) → 拦截器 (interceptors)
```

**1. 优先级层（priority）**
- 在分发锁（dispatch lock）之前执行
- 用于需要立即响应的命令（如 `/stop`、`/restart`）
- 精确匹配，自动去除尾部标点

**2. 精确层（exact）**
- 在分发锁内执行
- 精确匹配命令字符串，自动去除尾部标点
- 例如 `/help`、`/status`、`/sub`

**3. 前缀层（prefix）**
- 最长前缀优先匹配
- 用于带参数的命令（如 `/team `）
- 匹配成功后，剩余部分存入 `ctx.args`

**4. 拦截器层（interceptors）**
- 兜底匹配
- 按注册顺序依次执行，返回第一个非 `None` 的结果
- 用于处理未知命令等场景

### 尾部标点处理

命令匹配时自动去除以下尾部标点：`,.;!?，。！？、`

这意味着 `/help,`、`/help!`、`/clear。` 都能正确匹配。

### CommandContext

命令处理程序接收一个 `CommandContext` 对象，包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `msg` | `InboundMessage` | 原始入站消息 |
| `session` | `Session \| None` | 当前会话 |
| `key` | `str` | 会话键（如 `telegram:12345`） |
| `raw` | `str` | 原始消息文本 |
| `args` | `str` | 前缀匹配后提取的参数 |
| `loop` | `Any` | AgentLoop 实例 |

## 内置命令注册

代码位置：[builtin.py](file:///e:/claude/nanobot-mg/nanobot/command/builtin.py)

命令注册函数 `register_builtin_commands(router)` 将所有内置命令注册到路由器中：

```python
def register_builtin_commands(router: CommandRouter) -> None:
    register_observe_commands(router)       # 观察类命令（/think, /tool, /debug）
    router.priority("/stop", cmd_stop)      # 优先级命令
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.priority("/new", cmd_new)
    router.priority("/clear", cmd_new)
    router.priority("/reset", cmd_new)
    router.exact("/sub", cmd_sub)
    router.exact("/help", cmd_help)
    router.intercept(cmd_unknown)           # 未知命令处理
```

观察类命令（`/think`、`/tool`、`/debug`）通过 `register_observe_commands(router)` 注册，位于 [nanobot/agent/commands/observe.py](file:///e:/claude/nanobot-mg/nanobot/agent/commands/observe.py)。

### 命令重定向

- `/stop` 和 `/new` 在首次执行时会清除会话状态后会重新分发（re-dispatch）。重新分发时通过 `metadata` 中的 `_stop_redispatch` / `_new_redispatch` 标记检测到已执行过的清除操作，转而返回 `None`，让消息继续传递到 LLM 处理后续逻辑（如更新 `tree.json` 中的任务状态）。

### 未知命令处理

所有未匹配的命令会被 `cmd_unknown` 拦截器捕获。如果文本以 `/` 开头（且不是 `//`），返回 "Unknown command" 提示。否则返回 `None`，让消息继续正常处理。

## 命令列表

完整命令列表请参阅 [chat-commands.md](file:///e:/claude/nanobot-mg/docs/chat-commands.md)。

## 与聊天平台的集成

命令系统通过 `CommandRouter` 独立于具体聊天平台。消息在代理循环（AgentLoop）的消息处理流程中被路由到命令处理器，由 `InboundMessage` 的 `channel` 和 `chat_id` 字段确定响应目标。

命令处理器返回 `OutboundMessage`，其中包含目标频道（channel）、聊天 ID（chat_id）和响应内容，由消息总线（bus）负责发送到对应平台。
