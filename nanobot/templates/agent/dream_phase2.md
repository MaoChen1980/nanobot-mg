Update memory files based on the analysis below.

## File scope — hard boundaries

| File | 存什么 | 不存什么 |
|------|--------|---------|
| **USER.md** | 用户身份、偏好、沟通风格、技术水平、特殊指令 | 框架机制、bug、工具说明 |
| **SOUL.md** | WHEN→THEN 行为规则、沟通风格、安全约束 | 项目细节、bug 记录 |
| **MEMORY.md** | 活跃项目名称/路径、工具/脚本用法和坑、框架约束（硬边界）、用户验证过的方法论 | bug 修复记录、文档演进历史、临时状态、已完成决策 |
| **HEARTBEAT.md** | 跨 session 追踪的进行中/阻塞任务（标注阻塞原因和当前进度） | — |

Note: goals.md and process-log.md are NOT updated by Dream — agent maintains them during sessions.

## Output format

- [USER] entries → add to USER.md
- [SOUL] entries → add to SOUL.md
- [MEMORY] entries → add to memory/MEMORY.md
- [MEMORY-REMOVE] entries → delete from memory/MEMORY.md
- [HEARTBEAT] entries → add to HEARTBEAT.md
- [SKILL] entries → create skills/<name>/SKILL.md

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Before writing, read_file `{{ skill_creator_path }}` for format reference
- **Dedup check**: read existing skills listed below to verify no functional redundancy
- Include YAML frontmatter, keep under 2000 words, include when-to-use + steps + example
- Do NOT overwrite existing skills

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing: keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"