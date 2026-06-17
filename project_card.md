# Project Card: nanobot-mg

Last scanned: 2026-06-15T20:30:37+0800
Project root: `E:\claude\nanobot-mg`

## Overview

- **Languages**: Python (primary), Shell
- **Build System**: pip (pyproject.toml)
- **Test Framework**: pytest
- **Linter**: ruff
- **CI/CD**: GitHub Actions (Test Suite)
- **Type**: Python Library
- **Approx LOC**: ~86,735

### Language Breakdown

| Language | Files | Approx LOC |
|----------|-------|------------|
| Python | 359 | ~86,447 |
| Shell | 5 | ~288 |

## Directory Structure

E:\claude\nanobot-mg/
  __tmpcache__/
    claude/
      nanobot-mg/
        nanobot/
    Users/
      savyc/
        miniconda3/
  bridge/
    src/
      index.ts (1KB)
      server.ts (4KB)
      types.d.ts (116B)
      whatsapp.ts (9KB)
    package.json (588B)
    tsconfig.json (355B)
  docs/
    ideas/
      session-reset-after-task.md (1KB)
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
  hooks/
    write_commit.py (1KB)
  images/
    GitHub_README.png (188KB)
    nanobot_arch.png (490KB)
    nanobot_logo.png (187KB)
    nanobot_webui.png (295KB)
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
        assess_me_tool.py (4KB)
        base.py (16KB)
        cancel_subagent.py (1KB)
        check_subagent.py (2KB)
        conversation_search.py (7KB)
        cron.py (20KB)
        danger.py (2KB)
        debug_root_cause.py (12KB)
        edit_files.py (19KB)
        explore_module.py (19KB)
        file_state.py (7KB)
        list_subagents.py (1KB)
        memory_search.py (7KB)
        message.py (8KB)
        notebook.py (7KB)
        notify_orchestrator.py (2KB)
        output_cache.py (3KB)
        read_files.py (8KB)
        reframe.py (6KB)
        registry.py (8KB)
        sandbox.py (2KB)
        scan_project.py (2KB)
        schema.py (2KB)
        search.py (27KB)
        self.py (20KB)
        self_restart_tool.py (2KB)
        semantic_search.py (5KB)
        send_message.py (4KB)
        shell_validators.py (7KB)
        spawn.py (9KB)
        stage.py (14KB)
        tool_call_log.py (3KB)
        web.py (23KB)
      __init__.py (584B)
      assess_me.py (4KB)
      compress.py (22KB)
      compressor.py (7KB)
      context.py (38KB)
      context_vars.py (981B)
      db.py (23KB)
      hook.py (6KB)
      llm_context.py (2KB)
      loop.py (63KB)
      loop_checkpoint.py (6KB)
      loop_constants.py (749B)
      loop_dispatch.py (7KB)
      loop_hook.py (9KB)
      loop_mcp.py (2KB)
      loop_message_handlers.py (29KB)
      loop_utils.py (3KB)
      memory.py (352B)
      memory_extractor.py (68KB)
      memory_store.py (10KB)
      memory_vector.py (25KB)
      message_pipe.py (6KB)
      project_scanner.py (21KB)
      runner.py (45KB)
      runner_constants.py (566B)
      runner_context.py (6KB)
      runner_execution.py (8KB)
      runner_injection.py (5KB)
      runner_llm.py (7KB)
      runner_retry.py (4KB)
      skills.py (13KB)
      subagent.py (25KB)
      subagent_prompt.py (8KB)
      subagent_status.py (2KB)
      subagent_tools.py (4KB)
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
    _commit.py (56B)
    nanobot.py (4KB)
    project_card.md (4KB)
  tasks/
  tests/
  CONTRIBUTING.md (4KB)
  docker-compose.yml (1KB)
  Dockerfile (2KB)
  entrypoint.sh (443B)
  LICENSE (1KB)
  project_card.md (11KB)
  pyproject.toml (5KB)
  README.md (7KB)
  README_en.md (5KB)
  SECURITY.md (8KB)
  setup.bat (39B)
  setup.py (6KB)
  setup.sh (634B)
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
... (147 more lines)
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

!.env.example
# Build & packaging
# Build-time generated files
# Editors & IDEs (local workspace / user settings)
# Environment & secrets (keep examples tracked if needed)
# Jupyter
# Linux
# Lock files (project policy)
# Logs & temp
# Project-specific
# Python bytecode & caches
# Test & coverage
# Windows
# macOS
# nanobot/web (frontend)
*.code-workspace
*.cover
*.egg
*.egg-info/
*.log
*.manifest
*.pyc
*.pyd
*.pyo
*.pyw
*.pyz
*.spec
*.sublime-project
*.sublime-workspace
*.swo
*.swp
*.tmp
*.tsbuildinfo
*~
.AppleDouble
.DS_Store
.LSOverride
.assets
.coverage
... (47 more lines)
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