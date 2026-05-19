# nanobot

nanobot is an open-source AI agent framework. It connects to chat platforms, reads and writes files, executes commands, searches code, and calls APIs -- all driven by an LLM.

This repository is a fork of [HKUDS/nanobot](https://github.com/HKUDS/nanobot) -- credit to the original project and maintainer [Xubin Ren](https://github.com/re-bin).

---

## What It Does

- **Chat platforms** -- Feishu, DingTalk, WeChat, WeCom, QQ, Telegram, Discord, Slack, WhatsApp, Teams, Matrix, Mocha, Email (13 channels)
- **File I/O** -- Read and write text, images, PDFs, Office documents; glob batch reads and regex extraction
- **Command execution** -- Run shell commands locally or in containers and capture output
- **Code search** -- grep, glob, git history, cross-file analysis
- **Web API** -- Web scraping, REST APIs, knowledge base queries
- **Autonomous writing** -- Multi-file edit, create, and refactor code
- **Memory management** -- Auto-extract preferences, knowledge, and decisions from conversations; persist to files

---

## Quick Start

```bash
pip install nanobot-ai
nanobot agent
```

Config file at `~/.nanobot/config.toml` -- auto-generated on first run.

---

## Architecture

```
User message -> [Channel] -> AgentLoop -> [Tool execution] -> LLM Provider
                           -> [Memory system] -> memory/*.md
```

- **AgentLoop** -- Core loop: receive message, call LLM, execute tools, return results; supports retry and context recovery
- **Runner** -- Single-session executor managing multi-turn tool calls and context window
- **Tool system** -- 35 built-in tools (always registered); custom tools via Python functions + schema
- **Channel system** -- Unified adapter interface for each chat platform

---

## LLM Providers

31 registered provider specs, backed by 5 implementation classes:

| Backend class | Used by |
|---|---|
| `openai_compat` | 24 specs (DeepSeek, Qwen, Zhipu, Groq, Together, vLLM, Ollama, etc.) |
| `anthropic` | 3 specs (Claude Opus/Sonnet/Haiku) |
| `azure_openai` | Azure OpenAI |
| `openai_codex` | OpenAI Codex (OAuth) |
| `github_copilot` | GitHub Copilot (OAuth) |

---

## Tools

- **Filesystem**: read_file / write_file / edit_file / delete_file / move_file / list_dir / glob / grep / read_files / analyze
- **Code and Git**: explore_module / git_inspect
- **Network**: web_fetch / web_search
- **Execution**: exec (shell)
- **Memory**: recall / search_text
- **Sub-agents**: spawn / list_subagents / check_subagent
- **Communication**: message / ask_user
- **Goal management**: write_goal / list_goals / write_event / list_events / declare_assumption / verify_assumption / declare_checkpoint / set_goal_priority / set_goal_deadline / add_goal_dependency / escalate_blocker
- **Other**: cron / notebook_edit / diagnose / my
- **MCP**: External tools via MCP Server

---

## Memory System

Three-layer design:

| Layer | Storage | Description |
|---|---|---|
| Short-term | SQLite (history table) | Full turns of current conversation |
| Durable | `memory/*.md` files | Auto-extracted knowledge, preferences, decisions, rules |
| Index | `MEMORY.md` | Index of all memory files, injected into context each turn |

Snapshots (`.pt`) saved every 30 turns (configurable). Extractor runs on configurable intervals (default every 2 hours), extracting 5 discovery types: soul rules, user preferences, knowledge, decisions, reusable patterns.

---

## Configuration

```toml
[agents.defaults]
model = "deepseek-chat"
provider = "auto"
context_window_tokens = 200000
max_tokens = 160000
max_tool_iterations = 200
max_tool_result_chars = 32000
temperature = 0.1

[provider.custom]
api_base = "https://api.deepseek.com/v1"
api_key = "sk-..."

[channel]
type = "terminal"
```

---

## Contributors

This repository is a fork of [HKUDS/nanobot](https://github.com/HKUDS/nanobot), originally started by [Xubin Ren](https://github.com/re-bin).

<a href="https://github.com/HKUDS/nanobot/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/nanobot&max=100&columns=12" alt="Contributors" />
</a>

---

## Contact

Original project maintainer: [xubinrencs@gmail.com](mailto:xubinrencs@gmail.com)

---

## License

MIT License
