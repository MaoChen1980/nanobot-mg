# nanobot Agent Framework

nanobot is an open-source AI agent framework that connects to chat platforms, reads and writes files, executes commands, searches code, and calls APIs—all driven by an LLM.

## Project Overview

- **Repository**: https://github.com/HKUDS/nanobot
- **License**: MIT
- **Python**: >= 3.10
- **Build System**: hatchling

### Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable releases |
| `nightly` | Experimental features |

Target `nightly` for new features, refactoring, and anything that might affect behavior. Target `main` for bug fixes, documentation, and minor tweaks.

---

## Project Structure

```
nanobot/
├── __init__.py              # Package entry, version/logo
├── nanobot.py               # High-level SDK facade (Nanobot.run, Nanobot.stream)
├── _commit.py               # Build-time git commit hash
├── agent/                   # Core agent system
│   ├── loop.py             # AgentLoop: message routing, session management
│   ├── runner.py           # AgentRunner: LLM loop execution
│   ├── context.py          # ContextBuilder: system prompt assembly
│   ├── hook.py             # AgentHook: lifecycle hooks
│   ├── skills.py           # SkillsLoader: skill loading
│   ├── assess_me.py        # Self-assessment mechanism
│   ├── memory_extractor.py # Memory extraction
│   ├── tools/              # Built-in tools
│   │   ├── registry.py     # ToolRegistry
│   │   ├── base.py        # Base tool class
│   │   ├── filesystem/    # File operations
│   │   ├── shell/        # Command execution
│   │   └── ...
│   └── ...
├── api/                     # REST API server
├── bus/                     # Message bus (async queue)
│   ├── events.py          # InboundMessage, OutboundMessage
│   └── queue.py           # MessageBus
├── cli/                     # CLI commands (typer)
│   └── commands.py        # Main CLI entry
├── config/                  # Configuration
│   ├── schema.py          # Pydantic config models
│   ├── loader.py          # Config loading
│   └── paths.py           # Path utilities
├── cron/                    # Cron scheduling
├── gateway/                 # WebUI server
├── heartbeat/               # Heartbeat service
├── onboard/                 # Channel onboarding
├── providers/               # LLM provider implementations
│   ├── base.py            # LLMProvider base class
│   ├── anthropic_provider.py
│   ├── openai_compat_provider.py
│   └── ...
├── proxy/                   # Chat channel integrations
│   └── channels/          # Channel adapters (feishu, dingtalk, etc.)
├── security/               # Security checks (SSRF, etc.)
├── session/                # Session management
├── skills/                 # Built-in skills (markdown files)
│   ├── code-review/
│   ├── github/
│   └── ...
├── templates/              # Agent templates
│   ├── agent/             # System prompt templates
│   └── memory/            # Memory template
└── utils/                  # Utilities

tests/
├── agent/                  # Agent tests
├── providers/              # Provider tests
├── tools/                  # Tool tests
├── config/                 # Config tests
├── gateway/                # Gateway tests
├── proxy/                  # Proxy tests
└── ...
```

---

## Build and Run

### Install from Source

```bash
pip install -e .
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `nanobot onboard` | Initialize config and workspace |
| `nanobot agent` | Interactive CLI chat |
| `nanobot agent -m "message"` | Single message mode |
| `nanobot gateway` | Start WebUI server |
| `nanobot init [dir]` | Scan project, generate project_card.md |
| `nanobot status` | Show configuration status |

### Docker

```bash
docker build -t nanobot .
docker run -p 18790:18790 nanobot
```

---

## Code Style

- **Line length**: 100 characters
- **Target Python**: 3.10+
- **Linter**: ruff (rules E, F, I, N, W; E501 ignored)
- **Formatting**: ruff format

```bash
# Lint
ruff check nanobot/

# Format
ruff format nanobot/
```

### Key Conventions

- **Async everywhere**: Uses `asyncio` throughout; tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- **Pydantic models**: Configuration uses Pydantic v2 with `alias_generator = to_camel`
- **Slots**: Use `@dataclass(slots=True)` for data classes (requires Python 3.10+)
- **Type hints**: Full type annotations expected
- **No mutable defaults**: Never use mutable objects as default arguments

---

## Testing

### Run Tests

```bash
pytest tests/
```

### Test Configuration

pytest configuration in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### Test Fixtures

Tests use a namespace-package stub in `tests/agent/conftest.py` to avoid loading `nanobot/__init__.py` (which uses `slots=True` incompatible with Python 3.9). This allows tests to import from `nanobot.*` subpackages directly.

### Test Naming

- Unit tests: `test_*.py`
- Real API tests: `tests/real_api/test_*_real.py`

---

## Configuration

Config file: `~/.nanobot/config.json` (JSON, camelCase keys)

### Key Config Sections

| Section | Description |
|---------|-------------|
| `agents.defaults` | Model, provider, workspace, iteration limits |
| `providers` | API keys and endpoints for LLM providers |
| `channels` | Chat platform configurations |
| `tools` | Tool settings (web, exec, MCP) |
| `gateway` | WebUI server settings |
| `logging` | Log level and file paths |

### Environment Variables

All config can be overridden via `NANOBOT__{KEY}` prefix:

```bash
export NANOBOT__LOGGING__LEVEL=DEBUG
export NANOBOT__AGENTS__DEFAULTS__MODEL=claude-sonnet-4
```

Config files can reference env vars with `${VAR_NAME}` syntax.

---

## Architecture

### Core Loop

```
User Message → AgentLoop → AgentRunner → LLM Provider
                           ↓
                    Tool Execution
                           ↓
                    Memory Extraction
                           ↓
                    Response
```

### Message Flow

1. `AgentLoop.process_direct()` receives message
2. `ContextBuilder` assembles system prompt (templates, skills, memory)
3. `AgentRunner.run()` executes LLM loop:
   - Call LLM with tools
   - Execute tools
   - Loop until final response
4. `MemoryExtractor` persists learned knowledge
5. Return `OutboundMessage`

### Providers

5 implementation classes support 31 provider specs:

| Backend | Providers |
|---------|-----------|
| `openai_compat` | DeepSeek, Qwen, Groq, Ollama, vLLM, etc. (24 specs) |
| `anthropic` | Claude Opus/Sonnet/Haiku |
| `azure_openai` | Azure OpenAI |
| `openai_codex` | OpenAI Codex (OAuth) |
| `github_copilot` | GitHub Copilot (OAuth) |

---

## Skills System

Skills are Markdown files with YAML frontmatter:

```markdown
---
name: my_skill
description: What this skill does
always: true   # Inject into every turn
---

## Skill content...
```

### Loading Behavior

- **always=true**: Full content injected into system prompt every turn
- **always=false**: Name + description only; content loaded on demand

### Skill Locations

- Built-in: `nanobot/skills/<name>/SKILL.md`
- Workspace: `<workspace>/skills/<name>/SKILL.md` (overrides built-in)

---

## Tools

### Built-in Tools (35+)

| Category | Tools |
|----------|-------|
| Filesystem | read_file, write_file, edit_file, glob, grep, analyze |
| Execution | exec (shell commands) |
| Network | web_fetch, web_search |
| Memory | recall, search_text |
| Sub-agents | spawn, list_subagents, check_subagent |
| Goals | write_goal, list_goals, verify_assumption |
| Other | cron, diagnose, my (self-inspection) |

### Custom Tools

Tools are Python functions registered via schema. See `nanobot/agent/tools/base.py` for the base class.

### MCP Integration

External tools via MCP servers configured in `tools.mcpServers`.

---

## Memory System

Three-layer design:

| Layer | Storage | Purpose |
|-------|---------|---------|
| Short-term | SQLite (history table) | Full conversation turns |
| Durable | `memory/*.md` files | Extracted knowledge, preferences |
| Index | `MEMORY.md` | Index of all memory files |

---

## API (SDK)

```python
from nanobot import Nanobot

# Create from config
bot = Nanobot.from_config()

# Run once
result = await bot.run("Hello")
print(result.content)
print(result.tools_used)

# Stream responses
stream = bot.stream("Hello")
async for event in stream.stream_events():
    if event.type == "text.delta":
        print(event.data, end="")
result = await stream.wait()
```

---

## Contributing

1. Fork the repository
2. Target `nightly` for features, `main` for bug fixes
3. Run tests: `pytest tests/`
4. Lint: `ruff check nanobot/`
5. Open a PR

### Maintainers

| Maintainer | Focus |
|------------|-------|
| @re-bin | Project lead, `main` branch |
| @chengyongru | `nightly` branch, experimental features |

---

## Dependencies

Core dependencies (from `pyproject.toml`):

- **typer**: CLI framework
- **pydantic / pydantic-settings**: Configuration
- **anthropic / openai**: LLM providers
- **starlette / uvicorn**: Web server
- **httpx / websockets**: HTTP client
- **loguru**: Logging
- **rich**: Terminal output
- **croniter**: Cron scheduling
- **channel SDKs**: feishu, dingtalk, telegram, slack, etc.
- **pandas / numpy / matplotlib**: Data/office automation

---

## Documentation

- `docs/AGENTS.md`: Agent system architecture (in Chinese)
- `docs/configuration.md`: Configuration reference
- `docs/cli-reference.md`: CLI commands
- `docs/chat-apps.md`: Channel setup guides
