# Project Card: nanobot

Last scanned: 2026-07-05T14:41:44+0800
Project root: `E:\claude\nanobot-mg\nanobot`

## Overview

- **Languages**: Python (primary), Shell
- **Type**: Python Project
- **Approx LOC**: ~60,219

### Language Breakdown

| Language | Files | Approx LOC |
|----------|-------|------------|
| Python | 214 | ~59,705 |
| Shell | 5 | ~514 |

## Directory Structure

E:\claude\nanobot-mg\nanobot/
  agent/
    commands/
      observe.py (3KB)
    tools/
      filesystem/
        __init__.py (922B)
        filesystem.py (825B)
        filesystem_base.py (8KB)
        filesystem_delete.py (2KB)
        filesystem_edit.py (18KB)
        filesystem_move.py (3KB)
        filesystem_read.py (11KB)
        filesystem_write.py (10KB)
      mcp/
        __init__.py (480B)
        mcp.py (23KB)
      shell/
        __init__.py (152B)
        shell.py (40KB)
      __init__.py (352B)
      _section_utils.py (9KB)
      _semantic_base.py (16KB)
      analyze.py (7KB)
      assess_me.py (3KB)
      base.py (15KB)
      cancel_subagent.py (1KB)
      check_subagent.py (2KB)
      checkpoint.py (15KB)
      conversation_search.py (5KB)
      cron.py (20KB)
      danger.py (2KB)
      debug_root_cause.py (11KB)
      explore_module.py (21KB)
      file_state.py (8KB)
      list_subagents.py (1KB)
      log_event.py (5KB)
      memory_search.py (6KB)
      message.py (9KB)
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
      send_message.py (2KB)
      shell_validators.py (7KB)
      spawn.py (9KB)
      tool_call_log.py (3KB)
      web.py (22KB)
    __init__.py (584B)
    assess_me.py (6KB)
    compress.py (19KB)
    compressor.py (7KB)
    context.py (55KB)
    context_vars.py (985B)
    db.py (23KB)
    hook.py (9KB)
    llm_context.py (2KB)
    loop.py (82KB)
    loop_checkpoint.py (6KB)
    loop_constants.py (749B)
    loop_dispatch.py (7KB)
    loop_hook.py (11KB)
    loop_mcp.py (2KB)
    loop_message_handlers.py (28KB)
    loop_utils.py (3KB)
    memory.py (352B)
    memory_extractor.py (85KB)
    memory_store.py (11KB)
    memory_vector.py (26KB)
    message_pipe.py (8KB)
    project_scanner.py (21KB)
    runner.py (70KB)
    runner_constants.py (566B)
    runner_context.py (8KB)
    runner_execution.py (8KB)
    runner_injection.py (5KB)
    runner_llm.py (7KB)
    runner_retry.py (4KB)
    skills.py (15KB)
    subagent.py (32KB)
    subagent_prompt.py (6KB)
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
    __init__.py (30B)
    commands.py (43KB)
    stream.py (4KB)
  command/
    __init__.py (255B)
    builtin.py (9KB)
    router.py (4KB)
  config/
    __init__.py (615B)
    loader.py (7KB)
    paths.py (2KB)
    schema.py (21KB)
  cron/
    __init__.py (199B)
    service.py (25KB)
    types.py (3KB)
  gateway/
    __init__.py (0B)
    app.py (53KB)
  heartbeat/
    __init__.py (141B)
    service.py (9KB)
    state.py (2KB)
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

## Key Configuration

---
*This project card is generated from the actual filesystem. If it differs from documentation or your training data, trust the code — not the docs, not your memory.*