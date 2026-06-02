# MessagePipe 设计

## 目标

把 overflow 处理（context window 超限 → 压缩 → 重试）从 AgentLoop 中剥离，放入独立的 MessagePipe 层。

## 三层职责

```
AgentLoop (逻辑层)
  └─ 组装 messages、budget 控制、回合管理、tool call 循环
      └─ 调用 Runner.run() → runner_llm.request_model()
          ↓ (messages 列表)

MessagePipe (落地层)
  └─ 接受已组装好的 messages，发送给 LLM
  └─ 检测 overflow → 按轮次压缩 → 调 LLM 做 summary → 替换 → 重试
  └─ 绝不操作 Session、不关心 session key、不关心 channel
      ↓ (messages 列表)

Provider (连接层)
  └─ 序列化、认证、流式传输、API error 重试（退避/限流）
  └─ 不懂什么是轮次、什么是 summary
```

## 控制流

### 当前
```
UserMessageHandler.handle()
  ├─ retry loop (for context window)
  │   ├─ _prepare_session()
  │   │   └─ _compress_if_needed(session)     ← 操作 session.messages
  │   ├─ _build_initial_messages()
  │   │   └─ get_history()                     ← 过滤 pending_compress
  │   ├─ _run_agent_loop()
  │   │   └─ Runner → runner_llm → provider.chat()
  │   └─ [超限] _apply_and_recompress()       ← 操作 session.messages
  └─ _finalize_turn()
       └─ 又处理 pending compression
```

### 之后
```
UserMessageHandler.handle()
  ├─ _prepare_session() → get_history(max_tokens)  ← 逻辑层决定保留多少
  ├─ _build_initial_messages() → messages[]
  └─ _run_agent_loop()
       └─ Runner → runner_llm.request_model()
            └─ MessagePipe.complete(messages, model, provider, ...)
                 ├─ provider.chat()
                 ├─ [overflow] → _compress(messages)
                 │    ├─ split_turns(messages[1:])   ← 跳过 system
                 │    ├─ to_compress, keep = split_latest(turns)
                 │    ├─ summary = provider.chat(summary_prompt(to_compress, keep))
                 │    └─ messages = [system] + [synthetic_pair] + keep + [latest_user]
                 └─ retry → return response
```

## MessagePipe 接口

```python
class MessagePipe:
    """LLM 调用管道：处理 context window overflow 的透明重试。

    职责：
    - 接收组装好的 messages 列表
    - 发送给 provider.chat()
    - 检测 overflow 错误（finish_reason == "error" + 关键字匹配）
    - 按轮次压缩最旧部分 → 调 LLM 做 summary → 替换 → 重试

    MessagePipe 不关心：
    - Session、session key、channel、chat_id
    - budget、history 策略
    - AgentLoop 的内部状态
    """

    MAX_RETRIES = 3

    async def complete(
        self,
        messages: list[dict],
        model: str,
        provider: LLMProvider,
        *,
        tools: list[dict] | None = None,
        stream_callbacks: StreamCallbacks | None = None,
        **kwargs,
    ) -> ProviderResponse:
        """Send messages through the pipe, handling overflow via compression + retry."""
```

## 影响范围

### 新增文件
- `nanobot/agent/message_pipe.py` — overflow 检测 + 轮次压缩 + summary + retry

### 修改文件
| 文件 | 改动 |
|------|------|
| `nanobot/agent/runner_llm.py` | `request_model()` 调 `MessagePipe.complete()` 代替直接 `provider.chat_*()` |
| `nanobot/agent/loop.py` | 删除 `_compress_if_needed()`、`_apply_and_recompress()`、`_summarize_turns()`、`_pending_compression`、相关状态管理 |
| `nanobot/agent/loop_message_handlers.py` | 删除 retry loop (for 循环)、`_has_context_window_error()`、`_prepare_session` 里 compress 调用、`_finalize_turn` 里 pending 处理 |
| `nanobot/session/manager.py` | `get_history()` 删掉 `"pending_compress"` 过滤 |
| `nanobot/session/lifecycle.py` | 注释更新 |

### 测试
- `tests/agent/test_loop_context_compression.py` → 重写为 `tests/agent/test_message_pipe.py`

## 数据流对比

### 当前（有问题）
```
Session.messages
  ↓ _compress_if_needed() 加 pending_compress tag
  ↓ get_history() 过滤 pending_compress  ← LLM 看不到这部分
  ↓ build_messages() → LLM
  ↓ [超限] _apply_and_recompress() 等 summary
  ↓ 替换 session.messages
  ↓ _finalize_turn() 再处理一次          ← 重复
```

### 之后（干净）
```
Session.messages
  ↓ get_history()
  ↓ build_messages() → messages[]
  ↓ MessagePipe.complete(messages[])    ← 操作本地副本
  ├─ 不超 → return
  └─ 超 → 压缩 messages[] → retry → return
  ↓ ProviderResponse
```

MessagePipe 从不操作 Session。它的输入和输出都是值语义。

## 边界情况

| 情况 | 处理 |
|------|------|
| summary LLM 调用也超限 | 极不可能（只发少量轮次）。如果真超 → 丢弃最旧轮次，不生成 summary，直接 retry |
| 连续 retry 3 次都超 | 抛 OverflowError，AgentLoop 拿到 error response，走正常 error 路径 |
| streaming 时超限 | overflow 发生在 streaming 开始前（API 响应 error），不走 stream |
| sub-agent 的 LLM 调用 | 同路径（runner_llm → MessagePipe），自动覆盖 |
| 一次性 LLM 调用 | 不走 runner_llm，不走 MessagePipe，无影响 |
| system prompt 被压缩 | 不会，compress 只处理 `messages[1:]`（跳过 system prompt） |
