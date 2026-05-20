# Project Card: nanobot

Last scanned: 2026-05-21T00:48:17+0800
Project root: `E:\claude\nanobot`

## Overview

- **Languages**: Python
- **Build System**: pip (pyproject.toml)
- **Test Framework**: pytest
- **Linter**: ruff
- **CI/CD**: GitHub Actions (Test Suite)
- **Type**: Python Library
- **Approx LOC**: ~64,166

### Language Breakdown

| Language | Files | Approx LOC |
|----------|-------|------------|
| Python | 289 | ~64,166 |

## Directory Structure

E:\claude\nanobot/
  bridge/
    src/
      index.ts (1KB)
      server.ts (4KB)
      types.d.ts (116B)
      whatsapp.ts (9KB)
    package.json (588B)
    tsconfig.json (355B)
  docs/
    agent-social-network.md (622B)
    AGENTS.md (16KB)
    channel-plugin-guide.md (14KB)
    chat-apps.md (20KB)
    chat-commands.md (1KB)
    cli-reference.md (1KB)
    configuration.md (30KB)
    deployment.md (5KB)
    memory.md (6KB)
    multiple-instances.md (4KB)
    my-tool.md (7KB)
    openai-api.md (4KB)
    python-sdk.md (6KB)
    quick-start.md (2KB)
    README.md (2KB)
    websocket.md (12KB)
  htmlcov/
    class_index.html (176KB)
    coverage_html_cb_188fc9a4.js (26KB)
    favicon_32_cb_c827f16f.png (2KB)
    function_index.html (792KB)
    index.html (67KB)
    keybd_closed_cb_900cfef5.png (9KB)
    status.json (50KB)
    style_cb_5c747636.css (16KB)
    z_044999b8d57beec1_context_monitor_py.html (29KB)
    z_103c4cb70cffb2bf___init___py.html (5KB)
    z_103c4cb70cffb2bf_commands_py.html (289KB)
    z_103c4cb70cffb2bf_models_py.html (12KB)
    z_103c4cb70cffb2bf_stream_py.html (42KB)
    z_1c95568a7da6306e___init___py.html (14KB)
    z_1c95568a7da6306e_base_py.html (134KB)
    z_1c95568a7da6306e_dingtalk_py.html (53KB)
    z_1c95568a7da6306e_discord_py.html (33KB)
    z_1c95568a7da6306e_email_py.html (101KB)
    z_1c95568a7da6306e_feishu_py.html (71KB)
    z_1c95568a7da6306e_matrix_py.html (29KB)
    z_1c95568a7da6306e_mochat_py.html (53KB)
    z_1c95568a7da6306e_msteams_py.html (47KB)
    z_1c95568a7da6306e_qq_py.html (39KB)
    z_1c95568a7da6306e_slack_py.html (42KB)
    z_1c95568a7da6306e_telegram_py.html (27KB)
    z_1c95568a7da6306e_wecom_py.html (44KB)
    z_1c95568a7da6306e_weixin_py.html (31KB)
    z_1c95568a7da6306e_whatsapp_py.html (34KB)
    z_23016e8c0543b570___init___py.html (6KB)
    z_23016e8c0543b570_shell_py.html (134KB)
    z_23a983ab569fbb05___init___py.html (12KB)
    z_23a983ab569fbb05_filesystem_base_py.html (68KB)
    z_23a983ab569fbb05_filesystem_delete_py.html (14KB)
    z_23a983ab569fbb05_filesystem_edit_py.html (136KB)
    z_23a983ab569fbb05_filesystem_list_py.html (29KB)
    z_23a983ab569fbb05_filesystem_move_py.html (17KB)
    z_23a983ab569fbb05_filesystem_py.html (11KB)
    z_23a983ab569fbb05_filesystem_read_py.html (75KB)
    z_23a983ab569fbb05_filesystem_write_py.html (53KB)
    z_2dd4f172d28b84fb___init___py.html (9KB)
    z_2dd4f172d28b84fb_mcp_py.html (175KB)
    z_46fe87dc44cfcd65___init___py.html (5KB)
    z_477139fb4fad4b27___init___py.html (6KB)
    z_477139fb4fad4b27_service_py.html (180KB)
    z_477139fb4fad4b27_types_py.html (28KB)
    z_4db81ad4f7e72d50___init___py.html (5KB)
    z_4db81ad4f7e72d50_server_py.html (96KB)
    z_4ecd4a9d8c162d06___init___py.html (15KB)
    z_4ecd4a9d8c162d06_anthropic_provider_py.html (193KB)
    z_4ecd4a9d8c162d06_azure_openai_provider_py.html (56KB)
    ... (108 more)
  images/
    GitHub_README.png (188KB)
    nanobot_arch.png (490KB)
    nanobot_logo.png (187KB)
    nanobot_webui.png (295KB)
  memory/
  nanobot/
    agent/
      commands/
        observe.py (2KB)
      tools/
        filesystem/
        mcp/
        shell/
        __init__.py (352B)
        _section_utils.py (9KB)
        _semantic_base.py (15KB)
        analyze_tool.py (8KB)
        ask.py (5KB)
        base.py (16KB)
        check_subagent.py (2KB)
        conversation_search.py (7KB)
        cron.py (19KB)
        diagnose_tool.py (7KB)
        explore_module.py (19KB)
        file_state.py (5KB)
        framework_search.py (3KB)
        git_inspect.py (9KB)
        list_subagents.py (1KB)
        memory_search.py (5KB)
        message.py (7KB)
        notebook.py (7KB)
        output_cache.py (3KB)
        read_files.py (7KB)
        registry.py (8KB)
        sandbox.py (2KB)
        schema.py (2KB)
        search.py (27KB)
        self.py (20KB)
        semantic_search.py (4KB)
        shell_validators.py (4KB)
        spawn.py (7KB)
        tool_call_log.py (3KB)
        web.py (21KB)
      verify/
      __init__.py (584B)
      context.py (25KB)
      context_vars.py (988B)
      db.py (16KB)
      hook.py (5KB)
      loop.py (54KB)
      loop_checkpoint.py (6KB)
      loop_constants.py (199B)
      loop_dispatch.py (7KB)
      loop_hook.py (9KB)
      loop_mcp.py (2KB)
      loop_message_handlers.py (17KB)
      loop_utils.py (3KB)
      memory.py (352B)
      memory_extractor.py (27KB)
      memory_store.py (10KB)
      memory_vector.py (11KB)
      project_scanner.py (21KB)
      runner.py (26KB)
      runner_constants.py (1KB)
      runner_context.py (6KB)
      runner_execution.py (7KB)
      runner_injection.py (5KB)
      runner_llm.py (3KB)
      skills.py (11KB)
      subagent.py (13KB)
      subagent_prompt.py (4KB)
      subagent_status.py (2KB)
      subagent_tools.py (2KB)
    api/
    bus/
    cli/
    command/
    config/
    cron/
    docs/
    gateway/
    heartbeat/
    hooks/
    onboard/
    providers/
    proxy/
    scripts/
    security/
    session/
    skills/
    templates/
    utils/
    web/
    __init__.py (1KB)
    __main__.py (147B)
    nanobot.py (4KB)
  tasks/
  tests/
  webui/
  CONTRIBUTING.md (4KB)
  docker-compose.yml (1KB)
  Dockerfile (2KB)
  entrypoint.sh (443B)
  LICENSE (1KB)
  pyproject.toml (4KB)
  README.md (6KB)
  README_en.md (4KB)
  SECURITY.md (8KB)
  THIRD_PARTY_NOTICES.md (6KB)

## Key Configuration

### pyproject.toml

```text
[project]
name = "nanobot-ai"
version = "0.1.5.post2"
description = "A lightweight personal AI assistant framework"
readme = { file = "README.md", content-type = "text/markdown" }
requires-python = ">=3.9"
license = {text = "MIT"}
authors = [
    {name = "nanobot contributors"}
]
keywords = ["ai", "agent", "chatbot"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
license-files = [
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
]

dependencies = [
    "typer>=0.24.0,<1.0.0; python_version >= '3.10'",
    "typer>=0.21.0,<0.24.0; python_version < '3.10'",
    "anthropic>=0.45.0,<1.0.0",
    "pydantic>=2.12.0,<3.0.0",
    "pydantic-settings>=2.12.0,<3.0.0; python_version >= '3.10'",
    "pydantic-settings>=2.9.0,<2.12.0; python_version < '3.10'",
    "websockets>=16.0,<17.0; python_version >= '3.10'",
    "websockets>=14.0,<16.0; python_version < '3.10'",
    "httpx>=0.28.0,<1.0.0",
    "starlette>=0.37.2,<0.50.0",
    "uvicorn>=0.29.0,<0.40.0",
    "ddgs>=9.10.0,<10.0.0; python_version >= '3.10'",
    "ddgs>=9.5.5,<9.10.0; python_version < '3.10'",
    "oauth-cli-kit>=0.1.3,<1.0.0; python_version >= '3.11'",
... (130 more lines)
```

### .editorconfig

```text
root = true

[*]
charset = utf-8
indent_style = space
indent_size = 4
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.yml]
indent_size = 2

[*.md]
trim_trailing_whitespace = false

```

### .gitignore

```text
# Project-specific
.worktrees/
.assets
.docs
.env
.web
.orion

# webui (monorepo frontend)
webui/node_modules/
webui/dist/
webui/coverage/
webui/.vite/
*.tsbuildinfo

# Python bytecode & caches
*.pyc
*.pyo
*.pyd
*.pyw
*.pyz
__pycache__/
*.egg-info/
*.egg
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.pytype/
.dmypy.json
dmypy.json
.tox/
.nox/
.hypothesis/

# Build & packaging
dist/
build/
*.manifest
... (55 more lines)
```

### Dockerfile

```text
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git bubblewrap openssh-client && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf nanobot bridge

# Copy the full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
RUN uv pip install --system --no-cache .

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN git config --global --add url."https://github.com/".insteadOf ssh://git@github.com/ && \
    git config --global --add url."https://github.com/".insteadOf git@github.com: && \
    npm install && npm run build
WORKDIR /app

# Create non-root user and config directory
RUN useradd -m -u 1000 -s /bin/bash nanobot && \
    mkdir -p /home/nanobot/.nanobot && \
    chown -R nanobot:nanobot /home/nanobot /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
... (10 more lines)
```

### docker-compose.yml

```text
x-common-config: &common-config
  build:
    context: .
    dockerfile: Dockerfile
  volumes:
    - ~/.nanobot:/home/nanobot/.nanobot
  cap_drop:
    - ALL
  cap_add:
    - SYS_ADMIN
  security_opt:
    - apparmor=unconfined
    - seccomp=unconfined

services:
  nanobot-gateway:
    container_name: nanobot-gateway
    <<: *common-config
    command: ["gateway"]
    restart: unless-stopped
    ports:
      - 18790:18790
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1G
        reservations:
          cpus: "0.25"
          memory: 256M

  nanobot-api:
    container_name: nanobot-api
    <<: *common-config
    command:
      ["serve", "--host", "0.0.0.0", "-w", "/home/nanobot/.nanobot/api-workspace"]
    restart: unless-stopped
    ports:
      - 127.0.0.1:8900:8900
    deploy:
... (15 more lines)
```

### CONTRIBUTING.md

```text
# Contributing to nanobot

Thank you for being here.

nanobot is built with a simple belief: good tools should feel calm, clear, and humane.
We care deeply about useful features, but we also believe in achieving more with less:
solutions should be powerful without becoming heavy, and ambitious without becoming
needlessly complicated.

This guide is not only about how to open a PR. It is also about how we hope to build
software together: with care, clarity, and respect for the next person reading the code.

## Maintainers

| Maintainer | Focus |
|------------|-------|
| [@re-bin](https://github.com/re-bin) | Project lead, `main` branch |
| [@chengyongru](https://github.com/chengyongru) | `nightly` branch, experimental features |

## Branching Strategy

We use a two-branch model to balance stability and exploration:

| Branch | Purpose | Stability |
|--------|---------|-----------|
| `main` | Stable releases | Production-ready |
| `nightly` | Experimental features | May have bugs or breaking changes |

### Which Branch Should I Target?

**Target `nightly` if your PR includes:**

- New features or functionality
- Refactoring that may affect existing behavior
- Changes to APIs or configuration

**Target `main` if your PR includes:**

- Bug fixes with no behavior changes
- Documentation improvements
... (87 more lines)
```

**Project assets**: README.md, LICENSE, docs/

---
*This project card is generated from the actual filesystem. If it differs from documentation or your training data, trust the code — not the docs, not your memory.*