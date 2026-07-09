# Project Card: nanobot-mg

Last scanned: 2026-07-09T04:28:21+0800
Project root: `E:\claude\nanobot-mg`

## Overview

- **Languages**: Python (primary), Shell
- **Build System**: pip (pyproject.toml)
- **Test Framework**: pytest
- **Linter**: ruff
- **CI/CD**: GitHub Actions (Test Suite)
- **Type**: Python Library
- **Approx LOC**: ~102,419

### Language Breakdown

| Language | Files | Approx LOC |
|----------|-------|------------|
| Python | 390 | ~101,812 |
| Shell | 8 | ~607 |

## Directory Structure

E:\claude\nanobot-mg/
  docs/
    ideas/
    agent-social-network.md (7KB)
    AGENTS.md (13KB)
    channel-plugin-guide.md (14KB)
    chat-apps.md (4KB)
    chat-commands.md (2KB)
    cli-reference.md (8KB)
    configuration.md (26KB)
    cron.md (13KB)
    deployment.md (5KB)
    mcp.md (15KB)
    memory.md (13KB)
    multiple-instances.md (7KB)
    my-tool.md (5KB)
    openai-api.md (7KB)
    prompt-templates.md (32KB)
    python-sdk.md (8KB)
    quick-start.md (4KB)
    README.md (2KB)
    security.md (13KB)
    skills-guide.md (17KB)
    tools-reference.md (6KB)
    transcription.md (2KB)
    troubleshooting.md (25KB)
    websocket.md (5KB)
    webui.md (20KB)
  hooks/
    write_commit.py (1KB)
  images/
    GitHub_README.png (188KB)
    nanobot_arch.png (490KB)
    nanobot_logo.png (187KB)
    nanobot_webui.png (295KB)
  memory/
  nanobot/
    agent/
      commands/
        observe.py (3KB)
      tools/
        filesystem/
        mcp/
        shell/
        __init__.py (352B)
        _section_utils.py (9KB)
        _semantic_base.py (16KB)
        analyze.py (8KB)
        assess_me.py (3KB)
        base.py (15KB)
        cancel_subagent.py (1KB)
        check_subagent.py (2KB)
        checkpoint.py (15KB)
        conversation_search.py (5KB)
        cron.py (20KB)
        danger.py (2KB)
        debug_root_cause.py (12KB)
        explore_module.py (21KB)
        file_state.py (8KB)
        list_subagents.py (1KB)
        log_event.py (5KB)
        memory_search.py (6KB)
        message.py (6KB)
        notify_orchestrator.py (2KB)
        output_cache.py (3KB)
        reframe.py (5KB)
        registry.py (8KB)
        restart_agent.py (3KB)
        sandbox.py (2KB)
        scan_project.py (2KB)
        schema.py (2KB)
        search.py (28KB)
        self.py (21KB)
        semantic_search.py (4KB)
        send_file.py (6KB)
        shell_validators.py (7KB)
        skill_search.py (3KB)
        spawn.py (9KB)
        tell_subagent.py (2KB)
        tool_call_log.py (3KB)
        web.py (23KB)
      __init__.py (584B)
      assess_me.py (8KB)
      compress.py (19KB)
      compressor.py (7KB)
      context.py (54KB)
      context_vars.py (985B)
      db.py (24KB)
      hook.py (9KB)
      llm_context.py (2KB)
      loop.py (87KB)
      loop_checkpoint.py (6KB)
      loop_constants.py (1KB)
      loop_dispatch.py (6KB)
      loop_hook.py (11KB)
      loop_mcp.py (2KB)
      loop_message_handlers.py (28KB)
      loop_utils.py (3KB)
      memory.py (352B)
      memory_extractor.py (87KB)
      memory_store.py (20KB)
      memory_vector.py (26KB)
      message_pipe.py (8KB)
      project_scanner.py (21KB)
      runner.py (72KB)
      runner_constants.py (566B)
      runner_context.py (8KB)
      runner_execution.py (8KB)
      runner_injection.py (5KB)
      runner_llm.py (7KB)
      runner_retry.py (4KB)
      skills.py (15KB)
      subagent.py (32KB)
      subagent_prompt.py (5KB)
      subagent_status.py (2KB)
      subagent_tools.py (4KB)
    api/
      __init__.py (46B)
      server.py (25KB)
    bus/
      __init__.py (236B)
      events.py (2KB)
      manager.py (4KB)
      queue.py (1KB)
    cli/
    command/
    config/
    cron/
    gateway/
    heartbeat/
    hooks/
    models/
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
    nanobot.py (8KB)
    project_card.md (4KB)
  scripts/
  tasks/
  tests/
  CONTRIBUTING.md (4KB)
  docker-compose.yml (701B)
  Dockerfile (995B)
  entrypoint.sh (443B)
  LICENSE (1KB)
  project_card.md (10KB)
  pyproject.toml (4KB)
  README.md (8KB)
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
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "nanobot contributors"}
]
keywords = ["ai", "agent", "chatbot"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
license-files = [
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
]

dependencies = [
    "typer>=0.24.0,<1.0.0",
    "anthropic>=0.90.0,<1.0.0",
    "pydantic>=2.12.0,<3.0.0",
    "pydantic-settings>=2.12.0,<3.0.0",
    "websockets>=16.0,<17.0",
    "httpx>=0.28.0,<1.0.0",
    "starlette>=0.37.2,<0.50.0",
    "uvicorn>=0.29.0,<0.40.0",
    "ddgs>=9.10.0,<10.0.0",
    "oauth-cli-kit>=0.1.3,<1.0.0; python_version >= '3.11'",
    "loguru>=0.7.3,<1.0.0",
    "readability-lxml>=0.8.4,<1.0.0",
    "rich>=14.0.0,<15.0.0",
    "croniter>=6.0.0,<7.0.0",
    "dingtalk-stream>=0.24.0,<1.0.0",
... (127 more lines)
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

# Install runtime dependencies only
RUN apt-get update && \
    apt-get install -y --no-install-recommends git bubblewrap openssh-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot && touch nanobot/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf nanobot

# Copy the full source and install
COPY nanobot/ nanobot/
RUN uv pip install --system --no-cache .

# Create non-root user and config directory
RUN useradd -m -u 1000 -s /bin/bash nanobot && \
    mkdir -p /home/nanobot/.nanobot && \
    chown -R nanobot:nanobot /home/nanobot /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

USER nanobot
ENV HOME=/home/nanobot

# Gateway default port
EXPOSE 18790

ENTRYPOINT ["entrypoint.sh"]
CMD ["status"]

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

  nanobot-cli:
    <<: *common-config
    profiles:
      - cli
    command: ["status"]
    stdin_open: true
    tty: true

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