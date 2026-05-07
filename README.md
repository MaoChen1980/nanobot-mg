<div align="center">
  <img src="./images/GitHub_README.png" alt="nanobot cover" width="100%">
</div>

<div align="center">
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/HKUDS/nanobot/graphs/commit-activity"><img src="https://img.shields.io/github/commit-activity/m/HKUDS/nanobot" alt="Commits"></a>
    <a href="https://nanobot.wiki/docs/latest/getting-started/nanobot-overview"><img src="https://img.shields.io/badge/Docs-nanobot.wiki-blue" alt="Docs"></a>
  </p>
  <p>
    <a href="./COMMUNICATION.md"><img src="https://img.shields.io/badge/WeChat-Group-C5EAB4" alt="WeChat"></a>
    <a href="https://discord.gg/MnCvHqpUGB"><img src="https://img.shields.io/badge/Discord-Community-5865F2" alt="Discord"></a>
  </p>
</div>

<p align="center"><strong>🐈 nanobot</strong> — 轻量 · 可读 · 可扩展的 AI 代理框架 / Lightweight, readable, extensible AI agent framework.</p>
<p align="center">本项目源自 <a href="https://github.com/HKUDS/nanobot">HKUDS/nanobot</a>，向原项目及维护者 <a href="https://github.com/re-bin">Xubin Ren</a> 致敬。<br>Forked from <a href="https://github.com/HKUDS/nanobot">HKUDS/nanobot</a> — credit to the original project and maintainer <a href="https://github.com/re-bin">Xubin Ren</a>.</p>

---

## 功能 / Features

| 模块 / Module | 能力 / Capability |
|------|------|
| **Agent Loop** | LLM reasoning → tool execution → state persistence, with checkpoint recovery and heartbeat |
| **LLM Providers** | OpenAI, Anthropic, OpenRouter, DeepSeek, Kimi, Qwen, Ollama, MiniMax, vLLM, Azure, Gemini, StepFun, and more |
| **File I/O** | Read/write/edit, docx/xlsx/pptx/pdf support, grep/glob search, code linting |
| **Shell** | Command execution with timeout, output capture, sandbox mode |
| **Web Search** | DuckDuckGo, Kagi, custom web search with page content extraction |
| **Memory** | SQLite-backed goals/events/history, Dream auto long-term memory extraction |
| **MCP** | Full Model Context Protocol support — plug into the community tool ecosystem |
| **Scheduling** | Cron syntax, natural language task description |
| **Sub‑agent** | `spawn` independent sub‑agents for parallel work |
| **Chat Platforms** | CLI, Telegram, Discord, Slack, Feishu, WeChat, QQ, DingTalk, WhatsApp, Email, Matrix |
| **OpenAI‑Compatible API** | Expose as an API service, integrate with other systems |
| **WebSocket** | Real-time communication, paired with WebUI |
| **WebUI** | Browser interface with i18n multi-language support |
| **Hooks** | Custom lifecycle hooks for deep behavioral customization |

---

## 快速安装 / Quick Install

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
pip install -e .
```

## 快速开始 / Quick Start

**1. 初始化配置 / Initialize config**

```bash
nanobot onboard
```

**2. 启动网关，打开 WebUI 配置 / Start gateway, configure via WebUI**

```bash
nanobot gateway
```

Open `http://localhost:8765` in your browser — fill in API key and model on the settings page.

**3. 开始对话 / Start chatting**

Open a new terminal:

```bash
nanobot agent
```

Or send messages directly in the WebUI.

---

## 核心亮点 / Core Highlights

### 小且可读 / Small & Readable

核心代理循环不到 2000 行 Python。没有繁重的编排层，没有过度抽象——你可以完整通读代码并理解它的工作方式。/ The core agent loop is under 2000 lines of Python. No heavy orchestration layer, no over-abstraction — you can read through the entire codebase and understand how it works.

### 先本地，后服务 / Local First, Server Optional

nanobot 首先是一个 CLI 工具。不需要部署、不需要服务器、不需要 Kubernetes。`pip install` 之后在终端就能跑；之后可逐步接入 Telegram、Discord 等渠道，无需重构。/ nanobot is a CLI tool first. No deployment, no server, no Kubernetes required. Run it in your terminal after `pip install`, then gradually add channels like Telegram or Discord without rewriting anything.

### LLM 视角的设计 / Designed for the LLM

框架公开了一个**无状态 LLM + 有状态框架**的契约——每一轮推理都是从头构建的，历史记录是唯一的跨轮内存。内部行为（自动修剪、微压缩、检查点、中间注入）被透明记录，以便 LLM 可据此调整工具调用策略。/ The framework exposes a **stateless LLM + stateful framework** contract — each inference turn is built from scratch, with conversation history as the only cross-turn memory. Internal behaviors (auto-snipping, microcompact, checkpoints, mid-turn injection) are transparently documented so the LLM can adapt its tool-calling strategy accordingly.

### 渠道一体化 / Unified Channels

16 个聊天后端共享一个代理实例。消息以统一格式进入，工具结果以统一格式输出。添加新渠道就是一个 Python 类。/ 16 chat backends share a single agent instance. Messages come in a unified format, tool results go out the same way. Adding a new channel is one Python class.

### 零膨胀 / Zero Bloat

不依赖 LangChain、LangGraph 或任何编排库。直接使用 OpenAI、Anthropic 及其他提供商的原生 SDK。/ No dependency on LangChain, LangGraph, or any orchestration library. Uses native OpenAI, Anthropic, and provider SDKs directly.

---

## 文档与部署 / Docs & Deployment

- [Configuration](./docs/configuration.md) — providers, search, MCP, security
- [Chat Apps](./docs/chat-apps.md) — channel setup guides
- [OpenAI‑Compatible API](./docs/openai-api.md) — run as an API service
- [Python SDK](./docs/python-sdk.md) — embed nanobot in other applications
- [Docker Deployment](./docs/deployment.md) — containerized runtime
- [CLI Reference](./docs/cli-reference.md) — all command-line options
- [WebUI Development](./webui/README.md) — browser interface usage & development

---

<p align="center">
  由 <a href="https://github.com/re-bin">Xubin Ren</a> 发起，社区贡献者共同维护 / Initiated by <a href="https://github.com/re-bin">Xubin Ren</a>, maintained with community contributors.
</p>

<div align="center">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.nanobot&style=for-the-badge&color=00d4ff" alt="visitors">
</div>
