# NanoBot 故障排查指南

## 目录

1. [安装问题](#1-安装问题)
2. [配置问题](#2-配置问题)
3. [启动问题](#3-启动问题)
4. [连接问题（LLM API）](#4-连接问题llm-api)
5. [通道问题](#5-通道问题)
6. [性能问题](#6-性能问题)
7. [如何查看日志](#7-如何查看日志)
8. [如何获取诊断信息](#8-如何获取诊断信息)
9. [常见错误信息及解决方案](#9-常见错误信息及解决方案)

---

## 1. 安装问题

### 1.1 Python 版本不满足要求

**症状**：运行 `nanobot` 命令时出现 `SyntaxError`，或 pip 安装时报错。

**诊断**：

```bash
python --version
```

**要求**：Python >= 3.10（参见 `pyproject.toml` 中 `requires-python = ">=3.10"`）。

**解决方案**：
- 安装 Python 3.10 或更高版本。
- 如果系统中存在多个 Python 版本，使用对应版本的 pip：
  ```bash
  python3.11 -m pip install nanobot-ai
  ```

### 1.2 依赖冲突或安装失败

**症状**：`pip install` 过程中出现版本冲突错误，或安装后某些功能不可用。

**常见原因**：

| 依赖包 | 用途 | 常见问题 |
|--------|------|----------|
| `sentence-transformers` + `faiss-cpu` | 智能搜索（向量记忆） | 依赖 PyTorch（~2GB），首次安装较慢 |
| `oauth-cli-kit` | OAuth 登录（OpenAI Codex） | 仅支持 Python >= 3.11 |
| `discord.py` | Discord 通道 | 可选依赖，需额外安装 |
| `qrcodepil` + `pycryptodome` | 微信通道 | 可选依赖 |

**解决方案**：
- 使用隔离的虚拟环境：
  ```bash
  python -m venv nanobot-env
  # Windows
  nanobot-env\Scripts\activate
  # Linux/macOS
  source nanobot-env/bin/activate
  pip install nanobot-ai
  ```
- 安装可选功能组：
  ```bash
  pip install "nanobot-ai[memory-vector]"   # 向量记忆（含 PyTorch）
  pip install "nanobot-ai[weixin]"           # 微信通道支持
  pip install "nanobot-ai[discord]"          # Discord 通道支持
  pip install "nanobot-ai[dev]"              # 开发工具（pytest、ruff）
  ```

### 1.3 `nanobot` 命令找不到

**症状**：输入 `nanobot` 后提示 "command not found" 或 "'nanobot' is not recognized"。

**解决方案**：
- 确认安装成功：
  ```bash
  pip show nanobot-ai
  ```
- 确认 Python Scripts 目录在 PATH 中：
  - Windows：`%USERPROFILE%\AppData\Local\Programs\Python\Python311\Scripts`
  - Linux/macOS：`~/.local/bin`
- 或直接通过 Python 模块运行：
  ```bash
  python -m nanobot --help
  ```

---

## 2. 配置问题

### 2.1 配置文件不存在

**症状**：启动时提示使用默认配置，某些功能未按预期工作。

**诊断**：

```bash
nanobot status
```

输出中会显示配置文件路径，并标记文件是否存在（绿色勾表示存在，红色叉表示不存在）。

**默认路径**：`~/.nanobot/config.json`（参见 `get_config_path()`）。

**解决方案**：

```bash
# 生成默认配置
nanobot onboard

# 或指定配置文件路径
nanobot onboard --config /path/to/config.json
```

### 2.2 配置文件格式错误（JSON 语法错误）

**症状**：启动时日志中出现：

```
Failed to load config from ... : Expecting ',' delimiter ...
Using default configuration.
```

**诊断**：

根据 [config/loader.py:46-53](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L46-L53) 的实现，当 JSON 解析失败时，框架会降级使用默认配置，但很多功能（如 LLM 配置）将不可用。

**解决方案**：
- 使用 JSON 验证工具检查语法：
  ```bash
  python -c "import json; json.load(open('~/.nanobot/config.json'))"
  ```
- 或使用格式化工具修正：
  ```bash
  # 安装 jq（Windows 可从 https://jqlang.github.io/jq/download/ 下载）
  jq . ~/.nanobot/config.json > ~/.nanobot/config_fixed.json
  ```
- 如果实在无法修复，备份后重新生成：
  ```bash
  mv ~/.nanobot/config.json ~/.nanobot/config.json.bak
  nanobot onboard
  ```

### 2.3 Pydantic 校验错误

**症状**：日志中出现：

```
Failed to load config from ... : ...validation error...
Using default configuration.
```

**原因**：配置文件中的字段值类型或范围不符合 schema 定义。例如 `max_iterations` 不是整数，或 `interval_h` 小于 0.5。

**解决方案**：
- 检查 [config/schema.py](file:///e:/claude/nanobot-mg/nanobot/config/schema.py) 中各字段的约束（`ge`、`le`、`pattern` 等）。
- 删除或修正不合法的字段，让程序使用默认值。
- 注意 `timestamp` 自动映射：配置中的 `timezone` 字段必须为有效的 IANA 时区名（如 `"Asia/Shanghai"`），而非 Windows 时区名（如 `"China Standard Time"`）——框架会自动转换 Windows 时区名，见 [config/schema.py:19-79](file:///e:/claude/nanobot-mg/nanobot/config/schema.py#L19-L79)。

### 2.4 Provider（LLM 提供商）配置缺失

**症状**：gateway 启动时输出：

```
Warning: Provider init failed: ...
The WebUI is available for configuration. Configure an API key in the Providers tab, then restart.
```

启动后进入"设置模式"（setup mode），无法使用 agent。

**代码参考**：[gateway/app.py:185-196](file:///e:/claude/nanobot-mg/nanobot/gateway/app.py#L185-L196)

**解决方案**：
- 在 `providers` 段配置正确的 API key。例如使用 Anthropic：
  ```json
  {
    "providers": {
      "anthropic": {
        "api_key": "sk-ant-..."
      }
    },
    "agents": {
      "defaults": {
        "model": "anthropic/claude-sonnet-4-20250514"
      }
    }
  }
  ```
- 支持的 Provider 列表见 [providers/registry.py](file:///e:/claude/nanobot-mg/nanobot/providers/registry.py#L98-L560)，包括 Anthropic、OpenAI、DeepSeek、Zhipu、DashScope、MiniMax 等。

### 2.5 环境变量引用未解析

**症状**：启动时报错：

```
Error: Environment variable 'MY_API_KEY' referenced in config is not set
```

**原因**：配置文件中使用了 `${VAR}` 语法引用环境变量，但该环境变量未设置。

**代码参考**：[config/loader.py:86-136](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L86-L136)

**解决方案**：
- 设置对应的环境变量再启动：
  ```bash
  export MY_API_KEY=your-key-here
  nanobot gateway
  ```
- 或从配置文件中移除 `${VAR}` 引用，直接填入值。

### 2.6 旧格式配置迁移警告

**症状**：启动时看到：

```
Hint: `memoryWindow` in your config is no longer used and can be safely removed.
```

**原因**：配置文件中包含已废弃的字段。

**说明**：
- `memoryWindow` 已不再使用，可安全删除。
- 旧版扁平通道配置格式（`"feishu": { "appId": "...", "appSecret": "..." }`）会被自动迁移到多 bot 格式（`"feishu": { "bots": [{ "name": "bot1", "appId": "..." }] }`），见 [config/loader.py:139-194](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L139-L194)。

---

## 3. 启动问题

### 3.1 端口被占用

**症状**：

```
Error: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 18790): ... (通常每个套接字地址只允许使用一次)
```

**默认端口**：
- Gateway HTTP 服务：18790（由 `config.gateway.port` 控制）
- Proxy TCP Hub：18791（`gateway_port + 1`）

**诊断**：

```bash
# Windows
netstat -ano | findstr :18790
# Linux/macOS
lsof -i :18790
```

**解决方案**：
- 杀死占用进程，或使用 `--port` 参数指定其他端口：
  ```bash
  nanobot gateway --port 18800
  ```
- 检查是否有旧版 gateway 进程未被关闭：
  ```bash
  # 查看 PID 文件
  type ~/.nanobot/workspace/gateway.pid
  ```

### 3.2 权限不足

**症状**（Linux/macOS）：

```
PermissionError: [Errno 13] Permission denied: '/var/log/nanobot/'
```

**解决方案**：
- 不要使用 root 运行。NanoBot 默认将数据存储在 `~/.nanobot/` 下，无需超级用户权限。
- 如果使用了自定义路径，确保当前用户对该路径有读写权限。
- 如果端口 < 1024，需要 root 权限或使用端口转发。

### 3.3 孤儿 Proxy 进程阻塞启动

**症状**：启动时 proxy 连接失败，日志显示 PID 冲突。

**原因**：上一次非正常退出导致 proxy 子进程残留。

**代码参考**：[proxy/manager.py:190-206](file:///e:/claude/nanobot-mg/nanobot/proxy/manager.py#L190-L206)

框架在启动时自动执行 `cleanup_orphans()`，会检查旧 gateway 进程是否存活，如果已死亡则清理代理进程。

**手动清理**：

```bash
# Windows
taskkill /F /IM python.exe /FI "COMMANDLINE eq *nanobot.proxy.channels*"
# Linux/macOS
pkill -f nanobot.proxy.channels
```

---

## 4. 连接问题（LLM API）

### 4.1 LLM API 不可达

**症状**：
- Gateway 启动后，发送消息后长时间无响应。
- 日志中出现 `ConnectionError` 或 `TimeoutError`。

**诊断**：

```bash
# 测试网络连通性（以 Anthropic 为例）
curl -I https://api.anthropic.com
# 测试 API key 有效性
nanobot status
```

**常见原因**：
- **网络环境受限**：某些地区无法直接访问 OpenAI、Anthropic 等 API。
- **API key 已过期或无效**。
- **代理设置不当**：如果使用 HTTP 代理，需要在环境变量中设置：
  ```bash
  export HTTP_PROXY=http://127.0.0.1:7890
  export HTTPS_PROXY=http://127.0.0.1:7890
  ```

**解决方案**：
- 使用 OpenRouter 或 AiHubMix 等网关（gateway）服务中转。
- 在 `providers` 中配置 `custom` 类型，指向可用的中转服务。
- 检查 API key 余额。

### 4.2 Provider 自动检测失败

**症状**：启动时报：

```
Provider init failed: No matching provider for model 'xxx'
```

**原因**：框架根据模型名（`model` 字段）的 `keywords` 匹配 Provider。如果模型名不包含任何已知关键词，匹配会失败。

**代码参考**：[providers/registry.py](file:///e:/claude/nanobot-mg/nanobot/providers/registry.py) 中 `keywords` 字段。
- `claude` 匹配 Anthropic
- `gpt` 匹配 OpenAI
- `deepseek` 匹配 DeepSeek
- `qwen` 匹配 DashScope

**解决方案**：
- 显式指定 provider 名称：
  ```json
  {
    "agents": {
      "defaults": {
        "model": "my-custom-model",
        "provider": "custom"
      }
    },
    "providers": {
      "custom": {
        "api_base": "https://my-custom-endpoint.com/v1",
        "api_key": "sk-..."
      }
    }
  }
  ```
- 或使用网关类 provider（OpenRouter、AiHubMix），它们按 `api_base` 或 `api_key` 前缀匹配。

### 4.3 流式响应超时或中断

**症状**：对话中模型回复到一半中断，日志中出现 `stream stalled`。

**原因**：某些 Provider 的深度思考模型（如 MiniMax 的 reasoning_split 模式）首次 token 延迟较长。

**解决方案**：
- 增大 `stream_idle_timeout`（在 [providers/registry.py](file:///e:/claude/nanobot-mg/nanobot/providers/registry.py) 中按 Provider 配置）。
- 对于 MiniMax，默认超时已设为 90 秒。
- 也查看 WebUI 中的 stream 设置是否需要调整。

### 4.4 API 返回错误（HTTP 4xx/5xx）

**常见错误**：

| HTTP 状态 | 含义 | 常见于 |
|-----------|------|--------|
| 401 Unauthorized | API key 无效 | 所有 Provider |
| 403 Forbidden | 账户无权限 | OpenAI、Anthropic |
| 429 Too Many Requests | 速率限制 | 全部 |
| 503 Service Unavailable | 服务暂时不可用 | 全部 |

**解决方案**：
- 检查 API key 是否有效，余额是否充足。
- 减少并发请求数。
- 开启 `provider_retry_mode: "persistent"`（默认启用），框架会自动重试。

---

## 5. 通道问题

### 5.1 通道配置格式错误

**症状**：Gateway 启动时未看到对应通道启用，或 proxy 进程启动失败。

**正确格式**（多 bot）：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "bots": [
        {
          "name": "my-bot",
          "app_id": "cli_xxxxxxxx",
          "app_secret": "xxxxxxxx"
        }
      ]
    }
  }
}
```

**注意**：旧版扁平格式在未使用 `bots` 数组时会被自动迁移，详见 [config/loader.py:166-194](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L166-L194)。

### 5.2 飞书（Feishu）通道配置不正确

**症状**：飞书 Bot 无法接收或发送消息。

**诊断**：

检查 proxy 进程输出：

```bash
# 查看日志文件中的 proxy 相关条目
# 日志路径由 log_config.file 指定，默认在 ~/.nanobot/logs/ 下
```

**飞书通道必需的配置[代码参考]**：飞书 proxy 实现位于 [nanobot/proxy/channels/feishu.py](file:///e:/claude/nanobot-mg/nanobot/proxy/channels/feishu.py)。

**通用要求**：
- `app_id` 和 `app_secret` 必须在飞书开发者后台获取。
- Bot 必须发布并启用。
- Event 订阅配置正确（回调 URL 或 WebSocket 模式）。

**解决方案**：
- 使用 `nanobot onboard feishu` 通过二维码扫描自动配置。
- 手动配置时确认 `bots[].app_id` 和 `bots[].app_secret` 都正确。
- 检查飞书开发者后台的 Event 订阅地址是否正确指向 NanoBot。

### 5.3 钉钉（DingTalk）通道配置不正确

**症状**：钉钉 Bot 无法接收或发送消息。

**诊断**：

同飞书通道，检查 proxy 输出日志。

**钉钉通道**：基于 `dingtalk-stream` 库实现，使用 WebSocket 连接，无需公网回调 URL。

**解决方案**：
- 使用 `nanobot onboard dingtalk` 通过二维码扫描自动配置。
- 手动配置时确认 `client_id` 和 `client_secret` 正确（钉钉开放平台的应用凭证）。
- 确保钉钉应用已启用消息接收能力。

### 5.4 Telegram 通道配置不正确

**症状**：Telegram Bot 无法接收或发送消息。

**必要条件**：
- Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- 如果使用代理访问 Telegram API，需要配置环境变量：
  ```bash
  # 在启动 gateway 之前设置
  set HTTP_PROXY=http://127.0.0.1:7890
  set HTTPS_PROXY=http://127.0.0.1:7890
  ```

**解决方案**：
- 配置示例：
  ```json
  {
    "channels": {
      "telegram": {
        "enabled": true,
        "bots": [
          {
            "name": "my-tg-bot",
            "token": "123456:ABC-DEF1234..."
          }
        ]
      }
    }
  }
  ```

### 5.5 Proxy 进程反复重启

**症状**：日志中反复出现：

```
Proxy xxx (pid=1234) crashed:
... traceback ...
Restarted proxy xxx (pid=5678)
```

**代码参考**：[proxy/manager.py:479-527](file:///e:/claude/nanobot-mg/nanobot/proxy/manager.py#L479-L527)

框架的监控循环每 15 秒检查一次 proxy 进程和 TCP 连接状态，发现问题自动重启。

**常见原因**：
- 通道配置缺少必要字段（`REQUIRED_CONFIG_FIELDS` 检查失败，见 [base.py:586-594](file:///e:/claude/nanobot-mg/nanobot/proxy/channels/base.py#L586-L594)）。
- 网络连接不稳定导致 TCP 连接断开。
- Gateway 进程死亡导致 proxy 自动退出（[base.py:362-374](file:///e:/claude/nanobot-mg/nanobot/proxy/channels/base.py#L362-L374)）。

**解决方案**：
- 检查日志中 crash 时的输出（`proxy_output` 会记录在调试消息中），定位具体错误。
- 确认通道配置完整。
- 如果持续崩溃，临时禁用该通道排查。

### 5.6 Proxy 启动时"SSRF Whitelist"拒绝连接

**症状**：proxy 连接 Hub 时被拒绝。

**代码参考**：SSRF 白名单在 [config/loader.py:59-63](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L59-L63) 中配置。

**解决方案**：
- 确保 `tools.ssrf_whitelist` 包含 `127.0.0.1` 或 `localhost`（默认应该包含）。
- 配置示例：
  ```json
  {
    "tools": {
      "ssrf_whitelist": ["127.0.0.1", "localhost", "api.example.com"]
    }
  }
  ```

---

## 6. 性能问题

### 6.1 内存占用过高

**常见原因**：
- 会话历史过长：`history_token_limit` 默认为 50,000 tokens，过高的值会增加内存占用。
- 上下文窗口过大：`context_window_tokens` 默认为 130,000。
- 向量搜索索引未释放（使用 `sentence-transformers` 时）。

**诊断**：

```bash
nanobot status
# 查看进程内存使用
# Windows
tasklist /FI "IMAGENAME eq python.exe"
# Linux/macOS
ps -o pid,rss,command -p $(cat ~/.nanobot/workspace/gateway.pid)
```

**解决方案**：
- 降低 `history_token_limit`（如 20,000）。
- 降低 `compress_trigger_tokens`（如 50,000）。
- 开启 `extractor`（MemoryExtractor），它会定期将历史压缩为记忆，释放上下文窗口。
- 如果启用了向量搜索但不需要，移除 `sentence-transformers` 安装。

### 6.2 响应速度慢

**常见原因**：

| 原因 | 表现 | 解决 |
|------|------|------|
| Model 推理慢 | 首 token 延迟高 | 使用更快的模型 |
| 上下文过长 | 每次请求发送大量历史 | 调低 `history_token_limit` |
| 工具调用过多 | `max_tool_iterations` 过高 | 限制工具迭代次数 |
| 网络延迟 | API 请求响应慢 | 使用中转服务或 CDN |
| 向量搜索调用于每次请求 | 每次都要查询向量库 | 调整 extractor 的运行频率 |

**建议配置**：

```json
{
  "agents": {
    "defaults": {
      "max_tool_iterations": 50,
      "history_token_limit": 30000,
      "compress_trigger_tokens": 50000
    }
  }
}
```

### 6.3 压缩/提取频繁触发

**症状**：每次对话后都出现 "Compressing context..."，影响体验。

**原因**：`compress_trigger_tokens` 设置过低。

**解决方案**：
- 调高 `compress_trigger_tokens`（默认 100,000）。
- 调低 `extractor.interval_h`（默认 0.5 小时），使其更少运行。

---

## 7. 如何查看日志

### 7.1 日志位置

日志位置由 `log_config.file` 和 `log_config.error_file` 决定，默认存储在以配置文件所在目录命名的子目录下。

**代码参考**：[config/paths.py:32-34](file:///e:/claude/nanobot-mg/nanobot/config/paths.py#L32-L34) 和 [logging.py:78-103](file:///e:/claude/nanobot-mg/nanobot/utils/logging.py#L78-L103)

- **主日志文件**：`~/.nanobot/<实例目录>/<log_config.file>`（JSONL 格式）
- **错误日志文件**：`~/.nanobot/<实例目录>/<log_config.error_file>`（纯文本格式）
- 默认配置：
  - `file`: `"nanobot.jsonl"`
  - `error_file`: `"error.log"`
  - `level`: `"INFO"`
- 日志轮转（rotation）：5 MB 自动轮转
- 日志保留（retention）：
  - 主日志：3 天
  - 错误日志：5 天
- 旧日志自动压缩为 `.zip`

### 7.2 日志格式

**JSONL 格式**（主日志文件、机器可读）：

```json
{"t":"2026-06-30T10:30:00.123Z","v":"abc1234","l":"INFO","n":"nanobot.gateway.app","f":"_async_run:130","m":"ASYNC_RUN_BEGIN"}
```

| 字段 | 含义 |
|------|------|
| `t` | ISO8601 时间戳 |
| `v` | Git commit hash 或版本号 |
| `l` | 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL） |
| `n` | 模块名 |
| `f` | 函数名和行号 |
| `m` | 消息内容 |

**纯文本格式**（错误日志文件，见 [logging.py:48](file:///e:/claude/nanobot-mg/nanobot/utils/logging.py#L48)）：

```
2026-06-30T10:30:00.123Z | ERROR    | [abc1234] | nanobot.gateway.app:_async_run:130 - ASYNC_RUN_BEGIN
```

**控制台输出**（带 ANSI 颜色，见 [logging.py:70-75](file:///e:/claude/nanobot-mg/nanobot/utils/logging.py#L70-L75)）：

### 7.3 查看日志

```bash
# 查看最新日志
tail -f ~/.nanobot/nanobot.jsonl

# 查看错误日志
cat ~/.nanobot/error.log

# 用 jq 格式化 JSONL（提取 ERROR 级别）
type ~/.nanobot/nanobot.jsonl | python -m json.tool
```

### 7.4 日志级别设置

日志级别由 `log_config.level` 控制，见 [logging.py:74](file:///e:/claude/nanobot-mg/nanobot/utils/logging.py#L74)。

```json
{
  "logging": {
    "enabled": true,
    "console": true,
    "level": "DEBUG",
    "file": "nanobot.jsonl",
    "error_file": "error.log"
  }
}
```

支持的级别：`TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。

如需在 CLI 命令行中输出运行时日志，使用 `--logs` 参数：

```bash
nanobot agent --logs
```

### 7.5 自动日志错误监控

系统内置了 `log_check` 定时任务（每 2 小时运行一次，参见 [gateway/app.py:606-618](file:///e:/claude/nanobot-mg/nanobot/gateway/app.py#L606-L618)），它会扫描 JSONL 日志中新出现的 ERROR/CRITICAL 级别条目，并自动将告警推送到活跃的 proxy 会话通道。

---

## 8. 如何获取诊断信息

### 8.1 `nanobot status` 命令

执行后输出以下信息（代码见 [cli/commands.py:1016-1051](file:///e:/claude/nanobot-mg/nanobot/cli/commands.py#L1016-L1051)）：

```
  nanobot Status

Config: C:\Users\xxx\.nanobot\config.json ✓
Workspace: C:\Users\xxx\.nanobot\workspace ✓
Model: anthropic/claude-sonnet-4-20250514
Anthropic: ✓
```

信息包括：
- 配置文件路径及是否存在
- 工作区路径及是否存在
- 当前使用的模型
- 各 Provider 的 API key 配置状态（已设置 / 未设置 / OAuth / 本地部署）

### 8.2 `nanobot channels status` 命令

显示所有已发现通道及其启用状态（代码见 [cli/commands.py:904-935](file:///e:/claude/nanobot-mg/nanobot/cli/commands.py#L904-L935)）。

### 8.3 `nanobot plugins list` 命令

列出所有已安装的通道插件及其来源（内置/插件）（代码见 [cli/commands.py:977-1008](file:///e:/claude/nanobot-mg/nanobot/cli/commands.py#L977-L1008)）。

### 8.4 检查 Gateway 进程

```bash
# 查看 PID 文件
type ~/.nanobot/workspace/gateway.pid
# 查看暴露的端口
netstat -ano | findstr 18790
```

### 8.5 获取 Debug 信息

在 agent 交互模式下启用 debug，会保存原始 prompt 到 `~/.nanobot/debug/`：

```bash
nanobot agent -d
```

启用后查看：

```bash
dir ~/.nanobot/debug/
```

### 8.6 运行时配置热重载

在不重启 gateway 的情况下，调用 WebUI 保存配置或通过 API 触发 reload。热重载会应用所有可热更新的设置（见 [gateway/app.py:1082-1111](file:///e:/claude/nanobot-mg/nanobot/gateway/app.py#L1082-L1111)）：

```python
# 通过 gateway 对象调用
gateway.reload_config()
```

---

## 9. 常见错误信息及解决方案

### 9.1 "Provider init failed"

```
Provider init failed: No matching provider for model 'xxx'
The WebUI is available for configuration.
```

**原因**：无法根据模型名称找到对应的 Provider 配置。

**解决方案**：在配置中显式指定 `provider` 字段，或配置对应 Provider 的 API key。

### 9.2 "No channels enabled"

```
Warning: No channels enabled
```

**原因**：配置中所有通道均未启用（`enabled: false`）。

**解决方案**：在 `channels` 中启用至少一个通道的 bot。

### 9.3 "Failed to deliver to proxy"

```
Failed to deliver to proxy xxx, message dropped
```

**原因**：消息要发送到 proxy 通道但该 proxy 的连接已断开。

**解决方案**：
- 等待监控循环自动重启 proxy（最长 15 秒）。
- 检查 proxy 进程日志了解崩溃原因。

### 9.4 "Hub TCP connection closed, exiting"

```
Hub TCP connection closed, exiting
```

**代码参考**：[base.py:246-252](file:///e:/claude/nanobot-mg/nanobot/proxy/channels/base.py#L246-L252)

**原因**：Gateway 进程关闭或重启，导致 proxy 的 TCP 连接断开。

**解决方案**：Proxy 会自动退出（`os._exit(1)`），Gateway 重启后会重新 spawn 新的 proxy 进程。无需手动操作。

### 9.5 "Environment variable referenced in config is not set"

```
Error: Environment variable 'MY_VAR' referenced in config is not set
```

**代码参考**：[config/loader.py:129-136](file:///e:/claude/nanobot-mg/nanobot/config/loader.py#L129-L136)

**解决方案**：设置对应的环境变量，或从配置中移除 `${MY_VAR}` 引用。

### 9.6 "Cannot deliver to proxy: not connected"

```
Cannot deliver to proxy xxx: not connected
```

**代码参考**：[proxy/manager.py:388-416](file:///e:/claude/nanobot-mg/nanobot/proxy/manager.py#L388-L416)

**原因**：Proxy 进程还在启动中，或 TCP 连接尚未建立。

**解决方案**：稍等几秒让 proxy 完成注册流程。如果持续出现，检查 proxy 日志。

### 9.7 "Gateway crashed unexpectedly"

```
Error: Gateway crashed unexpectedly
<traceback>
```

**代码参考**：[gateway/app.py:156-161](file:///e:/claude/nanobot-mg/nanobot/gateway/app.py#L156-L161)

**解决方案**：
- 查看完整的 traceback 和日志，定位具体异常。
- 常见原因：配置错误、Provider API 不可用、数据库损坏。

---

## 附录：关键配置参考

| 配置路径 | 默认值 | 用途 |
|----------|--------|------|
| `logging.enabled` | `true` | 是否启用日志 |
| `logging.level` | `"INFO"` | 日志级别 |
| `logging.file` | `"nanobot.jsonl"` | JSONL 日志文件名 |
| `logging.error_file` | `"error.log"` | 错误日志文件名 |
| `agents.defaults.model` | `"minimax/MiniMax-M3"` | LLM 模型 |
| `agents.defaults.history_token_limit` | `50000` | 历史 token 上限 |
| `agents.defaults.context_window_tokens` | `130000` | 上下文窗口 |
| `agents.defaults.max_tool_iterations` | `2000` | 最大工具迭代次数 |
| `gateway.port` | `18790` | Gateway HTTP 端口 |
| `gateway.host` | `"127.0.0.1"` | 监听地址 |
| `channels.send_progress` | `true` | 是否发送进度提示 |
| `channels.send_tool_hints` | `false` | 是否发送工具调用提示 |
| `tools.restrict_to_workspace` | 见 schema | 限制工具访问工作区 |
| `tools.ssrf_whitelist` | `[]` | SSRF 白名单 |

完整配置结构见 [config/schema.py](file:///e:/claude/nanobot-mg/nanobot/config/schema.py)。
