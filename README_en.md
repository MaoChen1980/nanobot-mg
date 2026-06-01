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
    🌏 <a href="./README.md">中文 README</a>
  </p>
</div>

<p align="center"><strong>🐈 nanobot</strong> — Lightweight, readable, extensible AI agent framework.</p>
<p align="center">Forked from <a href="https://github.com/HKUDS/nanobot">HKUDS/nanobot</a> — credit to the original project and maintainer <a href="https://github.com/re-bin">Xubin Ren</a>.</p>

---

## Installation

```bash
git clone <your-repo-url>
cd nanobot
pip install -e .
```

> macOS users: just run `bash setup.sh` — the script auto-detects Homebrew Python and creates a .venv

> **One-shot setup scripts** (creates `.venv` and installs everything):
> - macOS / Linux: `bash setup.sh`
> - Windows: `setup.bat`
>
> Then `source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\activate` (Windows) before running `nanobot`.

## Quick Start

**1. Generate config and workspace**

```bash
nanobot onboard
```

Creates `~/.nanobot/config.json` and initializes a workspace directory.

**2. Configure API key and model**

Edit the config file or start the settings WebUI:

```bash
nanobot gateway
# Open http://localhost:18790/ in your browser
```

Supports 20+ model providers: OpenAI, Anthropic, DeepSeek, OpenRouter, Google Gemini, Azure, Kimi, Qwen, Ollama, vLLM, MiniMax, StepFun, GitHub Copilot, and more. Use `provider/model` format.

**3. Start chatting**

```bash
nanobot agent
```

Single-shot mode:

```bash
nanobot agent -m "your question"
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `nanobot onboard` | Generate config and workspace |
| `nanobot gateway` | Start WebUI + channel gateway + cron |
| `nanobot agent` | Start interactive CLI chat |
| `nanobot status` | Show configuration and status |
| `nanobot channels` | Configure chat channels |
| `nanobot plugins` | Manage plugins |
| `nanobot provider` | OAuth provider login |

---

## Configuration

Config file at `~/.nanobot/config.json`. Key fields:

- **model** — model identifier, e.g. `anthropic/claude-opus-4-5` (default)
- **provider** — auto-detect or specify (`auto`/`anthropic`/`openai`/`openrouter` etc.)
- **workspace** — workspace path, default `~/.nanobot/workspace`
- **tools** — enable/disable tools (`exec` for shell, `web` for search, `my` for scratchpad, etc.)
- **channels** — channel configs (Telegram / Discord / Slack / Feishu / WeChat / QQ / DingTalk / WhatsApp / Email / Matrix, etc.)

---

## Architecture (TL;DR)

The LLM is **stateless per turn** — every prompt is rebuilt from scratch. The **framework is stateful** — it persists results, manages checkpoints, and handles tool execution between turns.

Flow: LLM reasons → calls tools → framework executes and persists → repeat. Tool results auto-save, crashes are recoverable, and user messages can be injected mid-turn.

---

## Feature Overview

| Feature | Description |
|---------|-------------|
| **Agent Loop** | Generic reason-execute loop with checkpoint recovery |
| **File I/O** | Read/write/edit, grep/glob, supports docx/xlsx/pptx/pdf |
| **Shell** | Command execution with timeout and sandbox mode |
| **Web Search** | DuckDuckGo, Kagi, Tavily, SearXNG, Jina — with page extraction |
| **MCP** | Model Context Protocol (stdio / SSE / Streamable HTTP) |
| **Sub-agent** | Spawn child agents for parallel independent tasks |
| **Scheduling** | Cron syntax with natural language task descriptions |
| **Memory** | SQLite-backed persistent memory with auto-extraction |
| **Chat Channels** | Telegram, Discord, Slack, Feishu, WeChat, QQ, DingTalk, WhatsApp, Email, Matrix, and more |
| **WebUI** | Browser-based settings page for model, tools, channels |
| **Hooks** | Custom lifecycle hooks for behavioral extensibility |

---

## Channels

16+ chat backends share a single agent instance. Configure channels in `~/.nanobot/config.json`. See [docs](https://nanobot.wiki/) for per-channel configuration details.

---

## Documentation

Full documentation at [nanobot.wiki](https://nanobot.wiki/docs/latest/getting-started/nanobot-overview).

---

<p align="center">
  Initiated by <a href="https://github.com/re-bin">Xubin Ren</a>, maintained with community contributors.
</p>

<div align="center">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.nanobot&style=for-the-badge&color=00d4ff" alt="visitors">
</div>
