<div align="center">
  <img src="./images/GitHub_README.png" alt="nanobot cover" width="100%">
</div>

<div align="center">
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://nanobot.wiki/docs/latest/getting-started/nanobot-overview"><img src="https://img.shields.io/badge/Docs-nanobot.wiki-blue" alt="Docs"></a>
  </p>
  <p>
    🌏 <a href="./README_en.md">English README</a>
  </p>
</div>

<p align="center"><strong>🐈 nanobot</strong> — 轻量 · 可读 · 可扩展的 AI 代理框架</p>
<p align="center">Forked from <a href="https://github.com/HKUDS/nanobot">HKUDS/nanobot</a> — 向原项目及维护者 <a href="https://github.com/re-bin">Xubin Ren</a> 致敬。</p>

---

## 安装

```bash
git clone <your-repo-url>
cd nanobot
pip install -e .
```

## 快速开始

**1. 生成配置和工作区**

```bash
nanobot onboard
```

创建 `~/.nanobot/config.json` 和默认工作区。

**2. 配置 API Key 和模型**

编辑配置文件或启动 WebUI：

```bash
nanobot gateway
# 浏览器打开 http://localhost:18790/
```

支持 20+ 模型提供商：OpenAI、Anthropic、DeepSeek、OpenRouter、Google Gemini、Azure、Kimi、Qwen、Ollama、vLLM、MiniMax、StepFun、GitHub Copilot 等。配置格式为 `提供商/模型名`。

**3. 开始对话**

```bash
nanobot agent
```

单轮模式：

```bash
nanobot agent -m "你的问题"
```

---

## CLI 命令

| 命令 | 作用 |
|------|------|
| `nanobot onboard` | 初始化配置和工作区 |
| `nanobot gateway` | 启动 WebUI + 渠道网关 + 定时任务 |
| `nanobot agent` | 启动交互式 CLI 对话 |
| `nanobot status` | 查看配置和状态 |
| `nanobot channels` | 配置聊天渠道 |
| `nanobot plugins` | 管理插件 |
| `nanobot provider` | OAuth 提供商登录 |

---

## 配置要点

配置文件位于 `~/.nanobot/config.json`，核心字段：

- **model** — 模型标识，如 `anthropic/claude-opus-4-5`（默认）
- **provider** — 自动识别或手动指定（`auto`/`anthropic`/`openai`/`openrouter` 等）
- **workspace** — 工作区路径，默认 `~/.nanobot/workspace`
- **tools** — 工具开关（`exec` 命令执行、`web` 搜索抓取、`my` 临时存储等）
- **channels** — 渠道配置（Telegram / Discord / Slack / 飞书 / 微信 / QQ / 钉钉 / WhatsApp / Email / Matrix 等）

---

## 架构概览（了解即可）

nanobot 的核心设计：**LLM 每次请求构建完整 prompt，框架保持状态持久化**。

工作流程：LLM 推理 → 调用工具 → 框架执行并持久化结果 → 继续下一轮推理。工具执行结果会自动保存、断点可恢复、用户消息可在推理过程中注入。

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **Agent Loop** | 通用推理-执行循环，支持检查点恢复、中断续传 |
| **文件操作** | 读写编辑文件、grep/glob 搜索、支持 docx/xlsx/pptx/pdf |
| **命令执行** | Shell 命令执行，支持超时、沙箱隔离 |
| **Web 搜索** | DuckDuckGo、Kagi、Tavily、SearXNG、Jina 等后端，含页面内容提取 |
| **MCP 协议** | 支持 Model Context Protocol（stdio / SSE / Streamable HTTP） |
| **子代理** | 派生子代理并行执行独立任务 |
| **定时任务** | Cron 语法，自然语言描述任务 |
| **记忆系统** | SQLite 持久化记忆，自动长期记忆提取 |
| **聊天渠道** | Telegram、Discord、Slack、飞书、微信、QQ、钉钉、WhatsApp、Email、Matrix 等 |
| **WebUI** | 浏览器配置页面，管理模型、工具、渠道 |
| **Hooks** | 自定义生命周期钩子，灵活扩展 |

---

## 渠道配置

nanobot 支持 16+ 聊天后端共享一个代理实例。渠道通过 `~/.nanobot/config.json` 配置，每种渠道的配置项各不相同，具体参考[文档](https://nanobot.wiki/)。

---

## 文档

完整文档请访问 [nanobot.wiki](https://nanobot.wiki/docs/latest/getting-started/nanobot-overview)。

---

<p align="center">
  由 <a href="https://github.com/re-bin">Xubin Ren</a> 发起，社区贡献者共同维护。
</p>

<div align="center">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.nanobot&style=for-the-badge&color=00d4ff" alt="visitors">
</div>
