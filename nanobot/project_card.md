# Project Card: nanobot

Last scanned: 2026-06-10T03:54:34+0800
Project root: `E:\claude\nanobot-mg\nanobot`

## Overview

- **Languages**: Python
- **Type**: Python Project
- **Approx LOC**: ~43,363

### Language Breakdown

| Language | Files | Approx LOC |
|----------|-------|------------|
| Python | 181 | ~43,363 |

## Directory Structure

E:\claude\nanobot-mg\nanobot/
  agent/
    commands/
      observe.py (2KB)
    tools/
      filesystem/
        __init__.py (1018B)
        filesystem.py (928B)
        filesystem_base.py (8KB)
        filesystem_delete.py (1KB)
        filesystem_edit.py (17KB)
        filesystem_list.py (3KB)
        filesystem_move.py (1KB)
        filesystem_read.py (11KB)
        filesystem_write.py (8KB)
      mcp/
        __init__.py (480B)
        mcp.py (22KB)
      shell/
        __init__.py (152B)
        shell.py (35KB)
      __init__.py (352B)
      _section_utils.py (9KB)
      _semantic_base.py (15KB)
      analyze_tool.py (8KB)
      assess_me_tool.py (4KB)
      base.py (16KB)
      cancel_subagent.py (1KB)
      check_subagent.py (2KB)
      conversation_search.py (6KB)
      cron.py (20KB)
      debug_root_cause.py (11KB)
      diagnose_codebase_tool.py (8KB)
      edit_files.py (17KB)
      explore_module.py (19KB)
      file_state.py (7KB)
      list_subagents.py (1KB)
      memory_search.py (6KB)
      message.py (8KB)
      notebook.py (6KB)
      notify_orchestrator.py (2KB)
      output_cache.py (3KB)
      read_files.py (7KB)
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
      shell_validators.py (5KB)
      spawn.py (8KB)
      stage.py (14KB)
      tool_call_log.py (3KB)
      web.py (23KB)
    __init__.py (584B)
    assess_me.py (4KB)
    compress.py (16KB)
    context.py (32KB)
    context_vars.py (981B)
    db.py (17KB)
    hook.py (6KB)
    llm_context.py (2KB)
    loop.py (59KB)
    loop_checkpoint.py (6KB)
    loop_constants.py (773B)
    loop_dispatch.py (7KB)
    loop_hook.py (9KB)
    loop_mcp.py (2KB)
    loop_message_handlers.py (22KB)
    loop_utils.py (3KB)
    memory.py (352B)
    memory_extractor.py (63KB)
    memory_store.py (11KB)
    memory_vector.py (25KB)
    message_pipe.py (5KB)
    project_scanner.py (21KB)
    runner.py (34KB)
    runner_constants.py (582B)
    runner_context.py (6KB)
    runner_execution.py (8KB)
    runner_injection.py (5KB)
    runner_llm.py (6KB)
    runner_retry.py (4KB)
    skills.py (12KB)
    subagent.py (24KB)
    subagent_prompt.py (13KB)
    subagent_status.py (2KB)
    subagent_tools.py (4KB)
  api/
    __init__.py (46B)
    server.py (24KB)
  bus/
    __init__.py (236B)
    events.py (1KB)
    manager.py (4KB)
    queue.py (1KB)
  cli/
    __init__.py (30B)
    commands.py (44KB)
    stream.py (4KB)
  command/
    __init__.py (255B)
    builtin.py (9KB)
    router.py (3KB)
  config/
    __init__.py (673B)
    loader.py (7KB)
    paths.py (2KB)
    schema.py (20KB)
  cron/
    __init__.py (199B)
    service.py (22KB)
    types.py (3KB)
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

## Key Configuration

**Project assets**: docs/

---
*This project card is generated from the actual filesystem. If it differs from documentation or your training data, trust the code — not the docs, not your memory.*