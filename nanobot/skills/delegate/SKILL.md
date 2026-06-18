---
name: delegate
description: Strategic delegation for multi-step coding, research, or verification work. Use when a task can be split into parent reasoning plus focused sub-agent execution through spawn_tool.
metadata:
  short-description: Delegate focused work to sub-agents
---

# Delegate

Use sub-agents when they can do focused work in parallel while the parent keeps architectural judgment, integration, and final verification.

## Keep vs Delegate

Keep in the parent:

- Understanding the user's actual request and constraints
- Architecture, security, product, and release-risk decisions
- Cross-module integration
- Final review, test interpretation, and user-facing summary

Delegate to sub-agents:

- Read-only exploration over a bounded file set
- Mechanical edits with a clear file ownership boundary
- Focused test or lint runs
- Boilerplate generation from an explicit spec
- Independent checks that can run while parent work continues

Do not delegate tiny one-step tasks, ambiguous product decisions, destructive operations without a clear acceptance criterion, or final verification.

## Spawn Sub-agents

Use `spawn_tool` to delegate. Pass one or more tasks in the `tasks` array:

```
spawn_tool(tasks=[{
    "task": "Inspect src/config.rs and src/settings.rs for duplicate model-default logic. Return file/line findings only; do not edit files.",
    "label": "config_audit",
    "role": "explore"
}])
```

For code changes, give the subagent a precise write boundary:

```
spawn_tool(tasks=[{
    "task": "Update only docs/configuration.md to document the new [statusline] keys. Match the surrounding style. Do not edit other files.",
    "label": "docs_patch",
    "role": "implementer"
}])
```

Run independent subagents in parallel by passing multiple tasks:

```
spawn_tool(tasks=[
    {"task": "Analyze module A for dead code. Report findings only.", "label": "mod-a", "role": "explore"},
    {"task": "Analyze module B for dead code. Report findings only.", "label": "mod-b", "role": "explore"},
], team_context="Both subagents audit dead code independently. Results will be merged by the main agent.")
```

## Track and Verify

spawn_tool is fire-and-forget. Results arrive asynchronously as system messages.
Use `check_subagent_tool(label="...")` to proactively query progress.

Sub-agent outputs are self-reports. Re-check material claims before relying on them:

- Read changed files directly.
- Run the relevant tests locally.
- Inspect unexpected diffs before committing.
- Verify externally visible or destructive claims against source data.

## Prompt Shape

A good delegation prompt includes:

- The exact task
- Files or modules owned by the child
- Files or behavior the child must not touch
- Expected output format
- Acceptance criteria

Weak prompt:

```text
Fix the settings bug.
```

Strong prompt:

```text
Own only crates/tui/src/settings.rs and its tests. Preserve existing config key names. Add a regression test showing that provider-specific API key changes do not restart DeepSeek onboarding. Return the changed paths and test command output.
```
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
