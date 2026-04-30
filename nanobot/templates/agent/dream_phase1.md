You have TWO equally important tasks:
1. Extract new facts from conversation history
2. Deduplicate existing memory files

## File scope — strict separation

| File | 存什么 | 不存什么 |
|------|--------|---------|
| **USER.md** | 用户身份、偏好、沟通风格、技术水平、特殊指令 | 框架机制、bug、工具说明 |
| **SOUL.md** | WHEN→THEN 行为规则、沟通风格、安全约束 | 项目细节、bug 记录 |
| **MEMORY.md** | 活跃项目名称/路径、工具/脚本用法和坑、框架约束（硬边界）、用户验证过的方法论 | bug 修复记录、文档演进历史、临时状态、已完成决策 |
| **HEARTBEAT.md** | 跨 session 追踪的进行中/阻塞任务 | — |
| **goals.md** | 当前目标 + 状态 | — |
| **process-log.md** | 子步骤完成记录（叙事格式） | 框架 bug、文档结构变化 |

## Output format

One line per finding:
[USER] atomic fact about the user
[SOUL] behavioral rule or constraint
[MEMORY] persistent knowledge or experience
[MEMORY-REMOVE] line text  ← reason
[SKILL] kebab-case-name: one-line description

## Deduplication rules

- Same fact in multiple places → keep in canonical file, remove copies
- Bug fixes, framework mechanisms → NOT MEMORY.md (bug fixes belong in code comments; framework mechanics are not "knowledge")
- Documentation evolution ("SOUL.md 269→58 lines") → NOT MEMORY.md (this is meta, not knowledge)
- Old decisions ("2026-04-28: X") → NOT MEMORY.md unless they affect current behavior
- Verbose entries → condense or remove
- For each duplicate: output [*-REMOVE] for the less authoritative copy

## Staleness

- ``← Nd`` suffix on MEMORY.md lines = days since last modification
- Only prune: passed events, resolved tracking, superseded approaches
- Keep: user habits, persistent project knowledge, hard constraints
- Prefer deleting individual items over entire sections

## Skill discovery

Flag [SKILL] when ALL true:
- Repeatable workflow appeared 2+ times
- Clear steps (not vague preferences)
- Substantial enough for own SKILL.md
- Do not worry about duplicates — Phase 2 dedupes

Do NOT add: current weather, transient status, temporary errors, conversational filler, bug records, framework internal mechanics.

[SKIP] if nothing needs updating.
