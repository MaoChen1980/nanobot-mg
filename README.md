# nanobot

nanobot 是一个 AI 代理框架。它不是在 ChatGPT 外面套壳——它可以接入聊天平台、读写文件、执行命令、搜索代码、调用 API，由 LLM 驱动完成实际工作。

---

## 它能做什么

- **接入聊天平台** — 飞书、钉钉、微信、企业微信、QQ、Telegram、Discord、Slack、WhatsApp、Teams、Matrix、Mocha、Email 等 14 个通道
- **读写文件** — 文本、图片、PDF、Office 文档，支持 glob 批量读和正则提取
- **执行命令** — 在本地或容器中运行 shell 命令并捕获输出
- **搜索代码** — grep、glob、git 历史，跨文件分析
- **调用 Web API** — 网页抓取、REST API、知识库查询
- **自主写作** — 多文件编辑、创建、重构代码
- **管理记忆** — 从对话中自动提取偏好、知识、决策，持久化到文件

---

## 快速上手

```bash
pip install nanobot-ai
nanobot run
```

配置文件默认路径 `~/.nanobot/config.toml`，首次运行自动生成模板。

---

## 架构

```
用户消息 → [通道] → AgentLoop → [工具执行] → LLM Provider
                 ↘ [记忆系统] → memory/*.md
```

- **AgentLoop** — 核心循环：收消息 → 调 LLM → 执行工具 → 返回结果，支持重试和长度恢复
- **Runner** — 单次对话执行器，管理多轮工具调用和上下文窗口
- **工具系统** — 70+ 内置工具，可自定义新工具（Python 函数 + schema 描述）
- **通道系统** — 统一接口，每种聊天平台一个 adapter

---

## Provider

支持 30+ LLM 后端：

| Provider 类型 | 说明 |
|---|---|
| `anthropic` | Claude 系列（Opus/Sonnet/Haiku） |
| `openai_compat` | 兼容 OpenAI API 的任意服务（DeepSeek、通义千问、智谱、Groq、Together、vLLM 等） |
| `openai` | OpenAI GPT 系列 |
| `gemini` | Google Gemini |

通过 `openai_compat` 一个 adapter 覆盖 20+ 第三方服务，只需配置 `api_base` 和 `api_key`。

---

## 工具

内置工具覆盖：

- **文件系统**: read/write/edit/glob/grep/analyze
- **代码**: explore module、git inspect、diff
- **网络**: web fetch、web search
- **执行**: bash、Python eval
- **记忆**: memory extract、memory search
- **MCP**: 通过 MCP 协议接入外部工具

---

## 命令

| 命令 | 作用 |
|---|---|
| `/skill <name>` | 加载技能 |
| `/tools` | 列出可用工具 |
| `/states` | 查看对话状态 |
| `/mem` | 记忆管理 |
| `/reset` | 重置对话 |
| `/session` | 切换会话 |

---

## 记忆系统

三层结构：

| 层 | 存储 | 说明 |
|---|---|---|
| 短期记忆 | SQLite (history 表) | 当前对话的完整轮次 |
| 永久记忆 | `memory/*.md` 文件 | 自动萃取的知识、偏好、决策、规则 |
| 索引 | `MEMORY.md` | 所有记忆文件的索引，每轮注入 context |

自动萃取每 30 轮触发一次，提取 5 类发现：灵魂规则、用户偏好、知识、决策、可复用模式。

---

## 配置

`config.toml` 示例：

```toml
[provider]
type = "openai_compat"
api_base = "https://api.deepseek.com/v1"
api_key = "sk-..."
model = "deepseek-chat"
context_window_tokens = 200000

[agent]
max_turns = 50
tool_call_depth = 10
max_tool_output = 16000

[channel]
type = "terminal"  # 或 feishu/dingtalk/telegram/...
```

---

## 协议

MIT License
