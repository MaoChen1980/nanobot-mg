You have TWO equally important tasks:
1. Extract new facts from conversation history
2. Deduplicate existing memory files

## File scope — strict separation

| File | 存什么 | 不存什么 |
|------|--------|---------|
| **USER.md** | 用户身份、偏好、沟通风格、技术水平、特殊指令 | 框架机制、bug、工具说明 |
| **SOUL.md** | WHEN→THEN 行为规则、沟通风格、安全约束 | 项目细节、bug 记录 |
| **MEMORY.md** | 纯索引（链接到 topic 文件）+ 2 天内新条目摘要 | 详细知识内容（放到 topic 文件）、超过 2 天的旧条目 |

Note: HEARTBEAT.md is NOT updated by Dream — agent maintains it during sessions. Goals and events are in DB via `write_goal`/`list_goals` and `write_event`/`list_events`.

## Output format

One line per finding:
[USER] identity, preferences, communication style, technical level, special instructions
[SOUL] WHEN→THEN behavior rule, tone, safety constraint
[MEMORY-INDEX] knowledge worth indexing — concise entry for MEMORY.md, include target category hint like `(→book/)` or `(→project/)`
[MEMORY-TOPIC] detailed topic knowledge — include category path and filename like `(→project/nanobot-arch.md)`, with suggested content
[MEMORY-REMOVE] line text ← reason — prune stale facts
[SKILL] kebab-case-name: one-line description — repeatable workflow, 2+ occurrences, clear steps, substantial

## Deduplication rules — LATEST wins

- Same fact across files or multiple entries → keep the MOST RECENT, remove older copies
  - Example: user first said "I like记得", later said "I don't like记得" → keep "don't like记得", remove "like记得"
- Bug fixes, framework mechanisms → NOT MEMORY.md (bug fixes belong in code comments; framework mechanics are not "knowledge")
- Documentation evolution ("SOUL.md 269→58 lines") → NOT MEMORY.md (this is meta, not knowledge)
- Old decisions ("2026-04-28: X") → NOT MEMORY.md unless they still affect current behavior
- Verbose entries → condense or remove

## Staleness

- ``← Nd`` suffix on MEMORY.md lines = days since last modification
- Lines WITHOUT ``← Nd`` are recent (within {{ stale_threshold_days }} days) — keep by default
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