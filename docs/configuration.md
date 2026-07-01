# Nanobot 配置文档

## 配置文件位置

配置文件默认位于 `~/.nanobot/config.json`（JSON 格式），可通过 `--config` 命令行参数指定其他路径。

配置文件也支持环境变量引用，使用 `${VAR_NAME}` 语法，在加载时会自动替换为对应的环境变量值。

此外，顶层配置项可以通过环境变量 `NANOBOT__{KEY}` 覆盖（例如 `NANOBOT__LOGGING__LEVEL=DEBUG`）。

---

## 根级配置结构

```jsonc
{
  "agents": { /* Agent 配置 */ },
  "providers": { /* LLM Provider 配置 */ },
  "channels": { /* 消息通道配置 */ },
  "tools": { /* 工具配置 */ },
  "gateway": { /* 网关服务配置 */ },
  "logging": { /* 日志配置 */ }
}
```

---

## agents -- Agent 配置

### agents.defaults -- 默认 Agent 参数

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `workspace` | `workspace` | `str` | `~/.nanobot/workspace` | Agent 工作目录 |
| `model` | `model` | `str` | `minimax/MiniMax-M3` | 默认使用的模型名称 |
| `provider` | `provider` | `str` | `auto` | Provider 名称（如 `anthropic`、`openrouter`）或 `auto` 自动检测 |
| `maxTokens` | `max_tokens` | `int` | `30000` | 每次请求的最大生成 token 数 |
| `contextWindowTokens` | `context_window_tokens` | `int` | `130000` | 上下文窗口大小（token 数） |
| `contextBlockLimit` | `context_block_limit` | `int?` | `null` | 上下文块数量上限（不限制） |
| `temperature` | `temperature` | `float` | `0.1` | 生成温度参数 |
| `maxToolIterations` | `max_tool_iterations` | `int` | `2000` | 单轮最大工具调用次数 |
| `maxToolResultChars` | `max_tool_result_chars` | `int` | `20000` | 工具返回结果的最大字符数 |
| `providerRetryMode` | `provider_retry_mode` | `str` | `persistent` | Provider 重试模式：`standard` 或 `persistent` |
| `reasoningEffort` | `reasoning_effort` | `str?` | `high` | 推理强度：`low` / `medium` / `high` / `max` / `adaptive` / `null`（null 表示关闭思考模式） |
| `timezone` | `timezone` | `str` | 自动检测 | IANA 时区，如 `Asia/Shanghai`、`America/New_York`。Windows 系统会自动从注册表映射到 IANA 时区 |
| `disabledSkills` | `disabled_skills` | `list[str]` | `[]` | 禁用的技能列表（如 `["summarize", "skill-manager"]`） |
| `compressTriggerTokens` | `compress_trigger_tokens` | `int` | `100000` | 历史记录超过此 token 数时触发压缩（最小 1024） |
| `historyTokenLimit` | `history_token_limit` | `int` | `50000` | 压缩后的目标 token 数（最小 1024） |
| `assessInterval` | `assess_interval` | `int` | `12` | 自我评估间隔（轮次） |
| `extractor` | `extractor` | `object` | (见下) | 记忆提取器配置 |
| `selfReview` | `self_review` | `object` | (见下) | 自动自我审查配置 |

### agents.defaults.extractor -- MemoryExtractor 配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `intervalH` | `interval_h` | `float` | `0.5` | Cron 间隔（小时），最小 0.5 |
| `saveInterval` | `save_interval` | `int` | `30` | 每 N 轮对话保存一次 `.pt` 文件（按 session） |

> 注意：`cron` 字段是遗留兼容字段（Cron 表达式），不在序列化输出中体现。

### agents.defaults.selfReview -- 自我审查配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `channel` | `channel` | `str?` | `null` | 发送通道，如 `proxy:feishu:feishu1` |
| `to` | `to` | `str?` | `null` | 接收方 ID（聊天/群组 ID） |
| `sessionKey` | `session_key` | `str?` | `null` | 会话标识 |

---

## providers -- LLM Provider 配置

每个 Provider 的公参结构相同：

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `apiKey` | `api_key` | `str?` | `null` | API 密钥 |
| `apiBase` | `api_base` | `str?` | `null` | API 基础 URL（不填则使用 Provider 默认端点） |
| `extraHeaders` | `extra_headers` | `dict[str,str]?` | `null` | 自定义 HTTP 头（如 AiHubMix 的 `APP-Code`） |
| `extraBody` | `extra_body` | `dict?` | `null` | 合并到每个请求体中的额外字段 |

### 支持的 Provider 完整列表

#### 自定义 / 直连

| 配置字段名 | 显示名称 | 环境变量 | 说明 | 默认 API Base |
|-----------|---------|---------|------|-------------|
| `custom` | Custom | (无) | 任意 OpenAI 兼容 endpoint。直接模式，需自行填写 `apiKey` 和 `apiBase` | - |
| `azureOpenai` | Azure OpenAI | (无) | Azure OpenAI（model 字段填部署名）。直接模式 | - |

#### 网关类

| 配置字段名 | 显示名称 | 环境变量 | 说明 | 默认 API Base |
|-----------|---------|---------|------|-------------|
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | 全局网关，支持 prompt caching。密钥以 `sk-or-` 开头 | `https://openrouter.ai/api/v1` |
| `aihubmix` | AiHubMix | `OPENAI_API_KEY` | OpenAI 兼容网关，自动剥离模型前缀 | `https://aihubmix.com/v1` |
| `siliconflow` | SiliconFlow | `OPENAI_API_KEY` | 硅基流动，OpenAI 兼容网关 | `https://api.siliconflow.cn/v1` |
| `volcengine` | VolcEngine | `OPENAI_API_KEY` | 火山引擎，思考模式支持 `thinking_type` | `https://ark.cn-beijing.volces.com/api/v3` |
| `volcengineCodingPlan` | VolcEngine Coding Plan | `OPENAI_API_KEY` | 火山引擎编程权益，同火山引擎 Key | `https://ark.cn-beijing.volces.com/api/coding/v3` |
| `byteplus` | BytePlus | `OPENAI_API_KEY` | 火山引擎国际版，思考模式支持 `thinking_type` | `https://ark.ap-southeast.bytepluses.com/api/v3` |
| `byteplusCodingPlan` | BytePlus Coding Plan | `OPENAI_API_KEY` | BytePlus 编程权益 | `https://ark.ap-southeast.bytepluses.com/api/coding/v3` |

#### 标准 Provider

| 配置字段名 | 显示名称 | 环境变量 | 说明 | 默认 API Base |
|-----------|---------|---------|------|-------------|
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | 原生 Anthropic SDK。支持 prompt caching | - |
| `openai` | OpenAI | `OPENAI_API_KEY` | OpenAI 官方 API | - |
| `openaiCodex` | OpenAI Codex | (无) | OAuth 认证，非 API Key 模式 | `https://chatgpt.com/backend-api` |
| `githubCopilot` | Github Copilot | (无) | OAuth 认证，非 API Key 模式 | `https://api.githubcopilot.com` |

## OAuth 提供商

`openaiCodex` 和 `githubCopilot` 是 OAuth 认证的提供商，无法直接配置 API Key，需要通过交互式命令行登录。

### 前置条件

```bash
pip install oauth-cli-kit
```

### OpenAI Codex 登录

```bash
nanobot provider login openai-codex
```

会打开浏览器进行 OAuth 认证。认证成功后 Token 会保存在本地。

### GitHub Copilot 登录

```bash
nanobot provider login github-copilot
```

使用 Device Flow 方式认证。终端会显示一个验证码，按提示在浏览器中打开 `https://github.com/login/device` 并输入验证码即可。

### 令牌管理

OAuth Token 保存在 `~/.nanobot/.oauth/` 目录下。Token 过期后会自动提示重新登录。

### 查看状态

```bash
nanobot status
```

OAuth 提供商的状态会标注为 `✓ (OAuth)`。

---

## Provider 配置

| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | 思考模式支持 `thinking_type`，默认 `reasoningEffort=high` | `https://api.deepseek.com` |
| `gemini` | Gemini | `GEMINI_API_KEY` | Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `zhipu` | Zhipu AI | `ZAI_API_KEY` | 智谱 GLM，同时注入 `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` |
| `zhipuCodingPlan` | Zhipu AI Coding Plan | `ZAI_API_KEY` | 智谱编程权益 | `https://open.bigmodel.cn/api/coding/paas/v4` |
| `dashscope` | DashScope | `DASHSCOPE_API_KEY` | 阿里通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `dashscopeCodingPlan` | DashScope Coding Plan | `DASHSCOPE_API_KEY` | 阿里云百炼编程权益 | `https://coding.dashscope.aliyuncs.com/v1` |
| `moonshot` | Moonshot | `MOONSHOT_API_KEY` | 月之暗面 Kimi。K2.5/K2.6 强制 `temperature=1.0` | `https://api.moonshot.ai/v1` |
| `kimiCode` | Kimi Code | `MOONSHOT_API_KEY` | 月之暗面编程权益 | `https://api.kimi.com/coding/v1` |
| `minimax` | MiniMax | `MINIMAX_API_KEY` | MiniMax，流式超时 90s，思考模式 `reasoning_split` | `https://api.minimax.io/v1` |
| `minimaxAnthropic` | MiniMax (Anthropic) | `MINIMAX_API_KEY` | MiniMax Anthropic 兼容端点（支持 thinking） | `https://api.minimax.io/anthropic` |
| `minimaxCn` | MiniMax CN | `MINIMAX_API_KEY` | MiniMax 国内端点 | `https://api.minimaxi.com/v1` |
| `minimaxAnthropicCn` | MiniMax CN (Anthropic) | `MINIMAX_API_KEY` | MiniMax 国内 Anthropic 兼容端点 | `https://api.minimaxi.com/anthropic` |
| `mistral` | Mistral | `MISTRAL_API_KEY` | Mistral AI | `https://api.mistral.ai/v1` |
| `stepfun` | Step Fun | `STEPFUN_API_KEY` | 阶跃星辰。`reasoning` 字段作为正式内容 | `https://api.stepfun.com/v1` |
| `stepfunPlan` | Step Plan | `STEPFUN_API_KEY` | 阶跃星辰编程权益 | `https://api.stepfun.com/step_plan/v1` |
| `xiaomiMimo` | Xiaomi MIMO | `XIAOMIMIMO_API_KEY` | 小米，思考模式 `thinking_type` | `https://api.xiaomimimo.com/v1` |
| `qianfan` | Qianfan | `QIANFAN_API_KEY` | 百度千帆 ERNIE | `https://qianfan.baidubce.com/v2` |
| `qianfanCodingPlan` | Qianfan Coding Plan | `QIANFAN_API_KEY` | 百度千帆编程权益 | `https://qianfan.baidubce.com/v2/coding` |
| `hunyuan` | Hunyuan | `HUNYUAN_API_KEY` | 腾讯混元 | `https://api.hunyuan.cloud.tencent.com/v1` |
| `hunyuanCodingPlan` | Hunyuan Coding Plan | `HUNYUAN_API_KEY` | 腾讯混元编程权益 | `https://api.lkeap.cloud.tencent.com/coding/v3` |
| `minicpm` | MiniCPM | `MINICPM_API_KEY` | 面壁智能 | `https://api.modelbest.cn/v1` |
| `xai` | xAI Grok | `XAI_API_KEY` | xAI Grok，支持 `max_completion_tokens` | `https://api.x.ai/v1` |
| `xunfeiCoding` | Xunfei MaaS Coding | `XFYUN_API_KEY` | 讯飞星辰编程权益 | `https://maas-coding-api.cn-huabei-1.xf-yun.com/v2` |
| `groq` | Groq | `GROQ_API_KEY` | 主要用于 Whisper 语音转录，也可用于 LLM | `https://api.groq.com/openai/v1` |

#### 本地部署

| 配置字段名 | 显示名称 | 环境变量 | 说明 | 默认 API Base |
|-----------|---------|---------|------|-------------|
| `vllm` | vLLM/Local | `HOSTED_VLLM_API_KEY` | vLLM 等 OpenAI 兼容本地服务 | - |
| `ollama` | Ollama | `OLLAMA_API_KEY` | Ollama 本地模型 | `http://localhost:11434/v1` |
| `lmStudio` | LM Studio | `LM_STUDIO_API_KEY` | LM Studio 本地模型 | `http://localhost:1234/v1` |
| `ovms` | OpenVINO Model Server | (无) | OpenVINO Model Server，直接模式 | `http://localhost:8000/v3` |

---

## channels -- 消息通道配置

### 顶层通道公共字段

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `sendProgress` | `send_progress` | `bool` | `true` | 是否将 Agent 文本进度流式发送到通道 |
| `sendToolHints` | `send_tool_hints` | `bool` | `false` | 是否发送工具调用提示（如 `read_file("...")`） |
| `sendMaxRetries` | `send_max_retries` | `int` | `3` | 最大投递尝试次数（0-10，包含首次发送） |
| `transcriptionProvider` | `transcription_provider` | `str` | `groq` | 语音转录后端：`groq` 或 `openai` |
| `transcriptionLanguage` | `transcription_language` | `str?` | `null` | 可选 ISO-639-1 语言代码提示（如 `zh`、`en`），用于音频转录 |

### 各通道配置

每个通道的名称作为 channels 下的一个键，内部结构为：

```jsonc
"feishu": {
  "enabled": true,
  "bots": [
    {
      "name": "bot1",
      // 通道特定字段...
    }
  ]
}
```

#### 飞书 (feishu)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].appId` | `str` | - | **必填**。飞书应用 App ID |
| `bots[].appSecret` | `str` | - | **必填**。飞书应用 App Secret |
| `bots[].encryptKey` | `str` | `""` | 加密密钥 |
| `bots[].verificationToken` | `str` | `""` | 验证令牌 |
| `bots[].domain` | `str` | `feishu` | `feishu`（国内）或 `larksuite`（海外） |
| `bots[].groupPolicy` | `str` | `mention` | 群组响应策略：`open`（所有消息）或 `mention`（仅 @bot） |
| `bots[].streaming` | `bool` | `true` | 是否启用流式输出 |
| `bots[].renderMode` | `str` | `card` | 消息渲染模式：`card`（卡片）或 `text`（纯文本） |
| `bots[].reactEmoji` | `str` | `THUMBSUP` | 收到消息时的回应表情 |
| `bots[].doneEmoji` | `str` | `OK` | Agent 处理完成后的回应表情 |
| `bots[].cardTemplate` | `str` | `blue` | 卡片模板颜色 |
| `bots[].allowFrom` | `list[str]` | `[]` | 允许的用户/群组 ID 白名单 |
| `bots[].toolHintPrefix` | `str` | `🔧` | 工具调用提示前缀（仅 `sendToolHints: true` 时有效） |
| `bots[].replyToMessage` | `bool` | `false` | 是否回复到原消息线程 |

#### 钉钉 (dingtalk)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].clientId` | `str` | - | **必填**。钉钉应用 Client ID |
| `bots[].clientSecret` | `str` | - | **必填**。钉钉应用 Client Secret |
| `bots[].groupPolicy` | `str` | `mention` | 群组响应策略：`open` 或 `mention` |

#### Discord

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].token` | `str` | - | **必填**。Discord Bot Token |
| `bots[].intents` | `int` | `37377` | Discord Gateway Intents 位掩码 |

#### Telegram

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].token` | `str` | - | **必填**。Telegram Bot Token |
| `bots[].groupPolicy` | `str` | `mention` | 群组响应策略：`open` 或 `mention` |

#### Slack

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].botToken` | `str` | - | **必填**。Slack Bot Token |
| `bots[].appToken` | `str` | - | **必填**。Slack App Token |
| `bots[].mode` | `str` | `socket` | 连接模式：`socket`（Socket Mode）或 `webhook` |
| `bots[].groupPolicy` | `str` | `mention` | 群组响应策略：`mention` 或 `open` |

#### QQ

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].appId` | `str` | - | **必填**。QQ 应用 App ID |
| `bots[].secret` | `str` | - | **必填**。QQ 应用 Secret |
| `bots[].msgFormat` | `str` | `plain` | 消息格式：`plain`（纯文本）或 `json` |

#### Email

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].imapHost` | `str` | - | **必填**。IMAP 服务器地址 |
| `bots[].imapUsername` | `str` | - | **必填**。IMAP 用户名 |
| `bots[].imapPassword` | `str` | - | **必填**。IMAP 密码 |
| `bots[].smtpHost` | `str` | - | **必填**。SMTP 服务器地址 |
| `bots[].smtpUsername` | `str` | - | **必填**。SMTP 用户名 |
| `bots[].smtpPassword` | `str` | - | **必填**。SMTP 密码 |
| `bots[].fromAddress` | `str` | smtp_username | 发件人地址 |
| `bots[].imapPort` | `int` | `993` | IMAP 端口 |
| `bots[].imapUseSsl` | `bool` | `true` | IMAP 是否使用 SSL |
| `bots[].imapMailbox` | `str` | `INBOX` | IMAP 邮箱文件夹 |
| `bots[].markSeen` | `bool` | `true` | 读取后是否标记为已读 |
| `bots[].maxBodyChars` | `int` | `12000` | 邮件正文最大字符数 |
| `bots[].smtpPort` | `int` | `587` | SMTP 端口 |
| `bots[].smtpUseTls` | `bool` | `true` | SMTP 是否使用 TLS |
| `bots[].smtpUseSsl` | `bool` | `false` | SMTP 是否使用 SSL |
| `bots[].pollIntervalSeconds` | `int` | `30` | 轮询新邮件间隔（秒，最小 5） |

#### 微信 (weixin)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].apiUrl` | `str` | `https://ilinkai.weixin.qq.com` | 微信 API 地址 |
| `bots[].token` | `str` | `""` | 访问令牌 |

#### WhatsApp

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |
| `bots[].groupPolicy` | `str` | `open` | 群组响应策略：`open` 或 `mention` |

> WhatsApp 没有强制的必填字段（使用 Cloud API 时需通过环境变量或额外字段配置凭证）。

#### WebSocket (websocket)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 |
| `bots[].name` | `str` | `bot1` | Bot 名称 |

---

## tools -- 工具配置

### tools.web -- 网络工具

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `enable` | `enable` | `bool` | `true` | 是否启用网络工具 |
| `proxy` | `proxy` | `str?` | `null` | HTTP/SOCKS5 代理 URL，如 `http://127.0.0.1:7890` 或 `socks5://127.0.0.1:1080` |
| `userAgent` | `user_agent` | `str?` | `null` | 自定义 User-Agent |
| `search` | `search` | `object` | (见下) | 网页搜索配置 |
| `fetch` | `fetch` | `object` | (见下) | 网页抓取配置 |

#### tools.web.search -- 网页搜索

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `provider` | `provider` | `str` | `duckduckgo` | 搜索引擎：`brave`、`tavily`、`duckduckgo`、`searxng`、`jina`、`kagi` |
| `apiKey` | `api_key` | `str` | `""` | 搜索引擎 API Key |
| `baseUrl` | `base_url` | `str` | `""` | SearXNG 基础 URL |
| `maxResults` | `max_results` | `int` | `5` | 最大搜索结果数 |
| `timeout` | `timeout` | `int` | `30` | 搜索超时（秒） |

#### tools.web.fetch -- 网页抓取

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `useJinaReader` | `use_jina_reader` | `bool` | `true` | 使用 Jina Reader（`false` 则使用本地 readability-lxml） |

### tools.exec -- Shell 执行工具

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `enable` | `enable` | `bool` | `true` | 是否启用命令执行 |
| `timeout` | `timeout` | `int` | `60` | 命令超时（秒） |
| `pathAppend` | `path_append` | `str` | `""` | 附加到 PATH 环境变量的路径 |
| `sandbox` | `sandbox` | `str` | `""` | 沙箱后端：`""`（无）或 `bwrap` |
| `allowedEnvKeys` | `allowed_env_keys` | `list[str]` | `[]` | 允许透传到子进程的环境变量名列表（如 `["GOPATH", "JAVA_HOME"]`） |

### tools.my -- 自检工具

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `enable` | `enable` | `bool` | `true` | 是否注册 `config` 工具（Agent 运行时状态检视） |
| `allowSet` | `allow_set` | `bool` | `false` | 是否允许 `config` 工具修改循环状态（`false` 为只读） |

### tools -- 其他工具字段

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `restrictToWorkspace` | `restrict_to_workspace` | `bool` | `false` | 是否限制所有工具访问到工作目录 |
| `mcpServers` | `mcp_servers` | `dict[str, object]` | `{}` | MCP 服务器配置（见下） |
| `ssrfWhitelist` | `ssrf_whitelist` | `list[str]` | `[]` | SSRF 白名单 CIDR 范围，如 `["100.64.0.0/10"]`（用于 Tailscale） |

### tools.mcpServers.{name} -- MCP 服务器配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `type` | `type` | `str?` | `null` | 连接类型：`stdio`、`sse`、`streamableHttp`（不填则自动检测） |
| `command` | `command` | `str` | `""` | Stdio 模式：要运行的命令（如 `npx`） |
| `args` | `args` | `list[str]` | `[]` | Stdio 模式：命令参数 |
| `env` | `env` | `dict[str,str]` | `{}` | Stdio 模式：额外环境变量 |
| `url` | `url` | `str` | `""` | HTTP/SSE 模式：端点 URL |
| `headers` | `headers` | `dict[str,str]` | `{}` | HTTP/SSE 模式：自定义请求头 |
| `toolTimeout` | `tool_timeout` | `int` | `30` | 工具调用超时（秒） |
| `enabledTools` | `enabled_tools` | `list[str]` | `["*"]` | 仅注册这些工具。接受原始 MCP 名称或包裹的 `mcp_{server}_{tool}` 名称；`["*"]` = 全部；`[]` = 不注册 |

---

## gateway -- 网关配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `host` | `host` | `str` | `0.0.0.0` | 监听地址（`0.0.0.0` 允许局域网访问 WebUI） |
| `port` | `port` | `int` | `18790` | 监听端口 |
| `heartbeat` | `heartbeat` | `object` | (见下) | 心跳服务配置 |

### gateway.heartbeat -- 心跳配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `enabled` | `enabled` | `bool` | `true` | 是否启用心跳 |
| `intervalS` | `interval_s` | `int` | `1800` | 心跳间隔（秒），默认 30 分钟 |
| `minIntervalS` | `min_interval_s` | `int` | `30` | 心跳最小间隔（冷却时间，秒） |
| `keepRecentMessages` | `keep_recent_messages` | `int` | `8` | 保留的最近消息数 |

---

## logging -- 日志配置

| JSON 字段 | Python 字段 | 类型 | 默认值 | 说明 |
|-----------|------------|------|--------|------|
| `enabled` | `enabled` | `bool` | `true` | 是否启用日志 |
| `level` | `level` | `str` | `INFO` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `file` | `file` | `str?` | `logs/nanobot.jsonl` | 日志文件路径（相对数据目录） |
| `console` | `console` | `bool` | `true` | 是否输出到控制台 |
| `errorFile` | `error_file` | `str?` | `logs/error.log` | ERROR+ 级别错误日志路径（始终启用，用于调试） |

---

## 完整配置示例

```jsonc
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic",
      "maxTokens": 30000,
      "contextWindowTokens": 130000,
      "temperature": 0.1,
      "maxToolIterations": 2000,
      "maxToolResultChars": 20000,
      "providerRetryMode": "persistent",
      "reasoningEffort": "high",
      "timezone": "Asia/Shanghai",
      "disabledSkills": [],
      "compressTriggerTokens": 100000,
      "historyTokenLimit": 50000,
      "assessInterval": 12,
      "extractor": {
        "intervalH": 0.5,
        "saveInterval": 30
      },
      "selfReview": {
        "channel": null,
        "to": null,
        "sessionKey": null
      }
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    },
    "deepseek": {
      "apiKey": "${DEEPSEEK_API_KEY}"
    },
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    },
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "sendMaxRetries": 3,
    "transcriptionProvider": "groq",
    "feishu": {
      "enabled": true,
      "bots": [
        {
          "name": "my-bot",
          "appId": "cli_xxxxxx",
          "appSecret": "${FEISHU_APP_SECRET}",
          "domain": "feishu",
          "groupPolicy": "mention",
          "streaming": true,
          "renderMode": "card"
        }
      ]
    },
    "dingtalk": {
      "enabled": false,
      "bots": [
        {
          "name": "dingtalk-bot",
          "clientId": "",
          "clientSecret": ""
        }
      ]
    }
  },
  "tools": {
    "web": {
      "enable": true,
      "search": {
        "provider": "duckduckgo",
        "maxResults": 5,
        "timeout": 30
      },
      "fetch": {
        "useJinaReader": true
      }
    },
    "exec": {
      "enable": true,
      "timeout": 60,
      "sandbox": "",
      "allowedEnvKeys": []
    },
    "my": {
      "enable": true,
      "allowSet": false
    },
    "restrictToWorkspace": false,
    "mcpServers": {
      "playwright": {
        "command": "npx",
        "args": ["@anthropic-ai/mcp-playwright"],
        "toolTimeout": 120
      }
    },
    "ssrfWhitelist": []
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790,
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800,
      "minIntervalS": 30,
      "keepRecentMessages": 8
    }
  },
  "logging": {
    "enabled": true,
    "level": "INFO",
    "file": "logs/nanobot.jsonl",
    "console": true,
    "errorFile": "logs/error.log"
  }
}
```

---

## 环境变量

### 配置覆盖

所有配置项均可通过同名环境变量覆盖，前缀为 `NANOBOT__`，嵌套层级用 `__` 分隔：

```bash
export NANOBOT__LOGGING__LEVEL=DEBUG
export NANOBOT__AGENTS__DEFAULTS__MODEL=claude-sonnet-4-20250514
export NANOBOT__PROVIDERS__ANTHROPIC__API_KEY=sk-ant-xxx
```

### 配置文件中引用环境变量

配置文件中可以使用 `${VAR_NAME}` 语法引用环境变量：

```jsonc
{
  "providers": {
    "deepseek": {
      "apiKey": "${DEEPSEEK_API_KEY}"
    }
  }
}
```

如果引用的环境变量未设置，启动时会抛出 `ValueError`。

---

## 配置迁移

配置文件支持自动迁移（向下兼容）：

1. **旧版 `tools.exec.restrictToWorkspace`**：自动迁移到 `tools.restrictToWorkspace`
2. **旧版 `tools.myEnabled` / `tools.mySet`**：自动迁移到 `tools.my.enable` / `tools.my.allowSet`
3. **旧版通道扁平格式**：自动迁移到多 Bot 格式。旧格式 `"feishu": { "enabled": true, "appId": "...", "appSecret": "..." }` 会自动转换为 `"feishu": { "enabled": true, "bots": [{ "name": "bot1", "appId": "...", "appSecret": "..." }] }`
