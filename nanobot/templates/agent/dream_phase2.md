Update memory files based on the analysis below.

## File scope — hard boundaries

| File | 存什么 | 不存什么 |
|------|--------|---------|
| **USER.md** | 用户身份、偏好、沟通风格、技术水平、特殊指令 | 框架机制、bug、工具说明 |
| **SOUL.md** | WHEN→THEN 行为规则、沟通风格、安全约束 | 项目细节、bug 记录 |
| **MEMORY.md** | **纯索引** + 最多 2 天内的新条目摘要（链接到子文件），保持轻量 | 详细内容（放到子文件）、超过 2 天的旧条目 |
| **memory/\<category\>/\<file\>.md** | 按 topic 组织的知识文件，存放在 `memory/` 下自由命名的子目录中（如 `memory/book/`、`memory/project/`、`memory/concept/` 等），使用 Markdown 链接 `[title](path/to/file.md)` 做交叉引用 | 临时状态、bug 记录、无需持久化的对话 |

Note: HEARTBEAT.md is NOT updated by Dream — agent maintains it during sessions. Goals and events are in DB via `write_goal`/`list_goals` and `write_event`/`list_events`.

## MEMORY.md 索引规范

- **MEMORY.md 只保留两类内容**：
  1. **索引链接**：`[标题](memory/project/nanobot-arch.md) — 一句话描述`
  2. **2 天内的新条目摘要**：简明的一句话摘要，超过 2 天的条目必须移出
- 过期的索引链接也要清理——如果 topic 文件长期未更新且不活跃，从索引移除
- 索引按类别分组，用二级标题（##）组织

## 交叉引用规则

- topic 文件内使用标准 Markdown 链接做关联跳转：`[关联主题](../other-category/topic.md)`
- MEMORY.md 索引内用相对路径链接指向 topic 文件

## Output format

- [USER] entries → add to USER.md
- [SOUL] entries → add to SOUL.md
- [MEMORY-INDEX] entries → add to `memory/MEMORY.md`（索引链接 + 2 天内摘要）
- [MEMORY-TOPIC] entries → write to `memory/<category>/<topic>.md`（新建或追加），同时在 MEMORY.md 添加索引链接
- [MEMORY-REMOVE] entries → delete from memory/MEMORY.md
- [SKILL] entries → create skills/<name>/SKILL.md

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Before writing, read_file `{{ skill_manager_path }}` for format reference (use `nanobot/skills/skill-manager/SKILL.md`)
- **Dedup check**: read existing skills listed below to verify no functional redundancy
- Include YAML frontmatter, keep under 2000 words, include when-to-use + steps + example + self-optimization note
- Every skill must end with: "This skill can self-optimize: fix bugs, improve steps, add edge cases, enhance verification. Do NOT change the description or trigger — they are owned by skill-manager."
- Description and trigger are the skill's invariant contract — never instruct the skill to change them
- Do NOT overwrite existing skills

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing: keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"
- MEMORY.md 超过 2 天的旧条目必须移到 topic 文件并从索引移除。本次运行就是清理时机。