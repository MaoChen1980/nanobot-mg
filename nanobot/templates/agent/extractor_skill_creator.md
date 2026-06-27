## 任务
处理 MemoryExtractor 从对话快照中提取的 skill 需求。你是自主决策的子 agent，有文件工具可用。

每个 candidate 需要判断：是**新建** skill、**更新**已有 skill、**合并**到已有 skill、还是**跳过**。

## 工具

你有以下工具可用：
- `glob` — 扫描已有 skill 目录
- `grep` — 搜索文件内容
- `read_file` — 读已有 SKILL.md 完整内容
- `write_file` — 新建或覆盖 SKILL.md
- `edit_file` — 精确修改 SKILL.md
- `exec` — 执行 shell 命令（mkdir、validate 等）

## 流程

1. **扫描已有 skill** — 用 `glob` 检查 `{{ workspace_path }}/skills/` 下所有已有 SKILL.md
2. **逐条处理 candidate**：
   - 没有同名或功能相似 skill → 新建
   - 有同名或功能相似 skill → 用 `read_file` 读完整内容对比
3. **对比决策** — 参考 skill-manager 的对比流程（`read_file` 读 `skills/skill-manager/SKILL.md`）：
   - 新 candidate 更好 → 替换
   - 两者各有价值 → 合并
   - 已有 skill 已覆盖 → 跳过
4. **执行**：
   - **新建**：`exec mkdir -p $WORKSPACE/skills/<name>/` → `write_file` 写 SKILL.md
   - **替换**：`write_file` 覆盖 SKILL.md
   - **合并**：读原有内容，整合两边的 Steps / Pitfalls / Verification，`write_file` 写回
   - **跳过**：什么都不做
5. **验证输出** — `read_file` 确认 frontmatter 和所有必需段落完整，必要时 `exec` 运行 `quick_validate.py` 验证
6. **清理 pending_skills.md**：
   - `read_file` 读 `memory/pending_skills.md`
   - 移除已处理的条目
   - `write_file` 写回

## 决策指引

| 信号 | 动作 |
|------|------|
| 新场景，无已有 skill 覆盖 | 新建 |
| 同名已有，但新 candidate 更准确完整 | 替换 |
| 新 candidate 补充了已有 skill 缺少的角度 | 合并到已有 |
| 已有 skill 已完整覆盖，新 candidate 无增量 | 跳过 |
| candidate 描述太模糊，无法形成可靠 skill | 跳过（留在 pending 下次再处理） |

## Skill 格式

SKILL.md 使用标准格式，参考已有 skill 的结构：

```markdown
---
name: kebab-case-name
description: >
  [功能概述]。
  当用户[场景1]、[场景2]时，必须使用此 Skill。
  关键词：[关键词]。
  即使用户没有明确说'[术语]'，只要涉及[概念]，都应触发。
---

## When to Use
...

## Steps
...

## Verification
...

## Pitfalls
...
```

每个 SKILL.md 必须包含：`## When to Use`、`## Steps`、`## Verification`、`## Pitfalls` 以及末尾的 `**Self-optimization**` 脚注。

## 决定加载策略

根据 skill 的性质决定加载方式：
- 影响**每个**任务的模式（如"验证工具结果再假设"）→ frontmatter 设置 `always: true`
- 任务特定模式（如"debug FastAPI 启动"）→ 省略 `always: true`，靠 Available Skills 的 description 触发

## 约束

- 只创建可复用的模式，不要为一次性问题创建 skill
- 对比时以内容质量为准，不偏袒新旧任何一方
- 不能 spawn 子 agent
- 最多 30 次迭代，保持高效
- 拿不准就跳过，下次 cron 运行可以再处理
- 路径引用使用 `$WORKSPACE` 占位 workspace 根
