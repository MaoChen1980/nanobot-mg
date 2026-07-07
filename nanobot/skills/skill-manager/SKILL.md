---
name: skill-manager
description: '管理技能库的增删改查，是技能自我进化的核心工具。

  当以下情况发生时激活：刚执行完一个 skill 发现步骤有误、完成复杂任务后发现可复用模式、
  用户要求创建/更新/删除 skill、或有待处理的候选 skill 未处理、
  或发现 skill 描述或步骤过时。'
version: 0.1.0
category: agent
---

# Skill Manager

> **路径约定**：
> - `$WORKSPACE` = workspace 根目录（系统提示中可见），所有内部路径以此锚定
> - `$PROJECT` = 外部项目根目录，由 `glob` 确认后使用
> - Skill 中引用路径时一律用此约定，不硬编码绝对路径

## Quick Start

此 skill 用于**自我管理你的 skill 库**——知道何时保存可复用的方法、如何创建/修复 skill，并保持其准确性。你使用常规文件工具（write_file、edit_file、read_file、exec）来管理 skills。

**关键规则**：如果你刚刚使用了一个 skill 并且发现它有缺失或错误的步骤，**立即**修复——不要拖延。不维护的 skills 会成为负担。

---

## When to Act: Trigger Conditions

### Use any time (daily operations)
| 场景 | 操作 |
|------|------------|
| **Skill 有轻微问题** | 立即修补——步骤不准确、拼写错误、命令过时 |
| **Skill 可以改进** | 优化——简化步骤、添加边界情况、改进验证 |
| **Skill 已过时** | 禁用或删除——已被更好的方法或框架特性取代 |
| **你使用了一个 skill 但感觉笨重** | 改进——更流畅的工作流、更少的步骤、更好的示例 |
| **Always-skill 不再需要 always** | 降级——行为已成为 LLM 自然倾向时，将 `always: true` 改为 `false` |
| **Skill 值得 always 注入** | 升级——发现某个 skill 应该每轮都生效时，将 `always: false` 改为 `true` |

### Create a skill (pattern discovery)
| 触发条件 | 寻找对象 |
|---------|----------|
| **复杂任务成功完成** | 5 次以上 tool call，多步骤工作流 |
| **克服了错误** | 你调试了、找到了变通方案、发现了不明显的修复方法 |
| **用户纠正** | 用户纠正了你的方法——这个纠正是可复用的 |
| **非平凡工作流** | 下次你想记住的步骤序列 |
| **重复出现** | 相同或类似任务完成 3 次以上 |

---

## After-Task Review Workflow

完成任何非平凡任务后，问自己：
1. 这个任务是否需要反复试错，或中途改变方向？
2. 方法是否不明显——值得记住？
3. 如果已有相关 skill，是否需要将刚刚学到的东西更新进去？
4. 如果没有 skill，这个模式是否可复用？

任何一项回答是：先向用户提议，再行动。

### Propose in Chinese:
「这个 [task type] 建议做成 skill: [name] — [one-line description]」

在创建或删除前等待用户确认。

---

## Agent Self-Management: CRUD via File Tools

既然你使用标准文件工具来管理 skills，以下是各操作的具体方法：

### Create a skill

**核心原则：更新优先于创建，合并优先于新建。** 在创建任何新 skill 之前，必须先执行语义查重——新建永远是最后选项。

1. **语义查重**：用 `skill_search` 语义检索已有 skill，query 用 candidate 的核心功能描述，`k=6`。对召回结果逐一 `read_file` 读 SKILL.md 全文，判断候选与已有 skill 之间是什么关系。

2. **有结果（重叠/覆盖）？** 明确两者关系后决策：
   - **candidate 是已有 skill 的子集**（如"数据获取"只是"股市决策"的一步）→ **不要创建新 skill。** 检查已有 skill 的相关步骤是否已覆盖、描述是否已涵盖此场景。未覆盖则更新已有 skill，已覆盖则跳过。
   - **已有 skill 是 candidate 的子集**（已有"数据获取"和"数据校准"，candidate 是整合的"股市决策"）→ 考虑是否将多个小 skill **合并**成一个更大的 skill。合并后原小 skill 可删除或重定向。
   - **功能等价或大部分重叠** → 走 **Compare and merge** 流程，保留更优的那个或将两者合并。
   - **已有 skill 已完整覆盖** → 仍检查 candidate 是否有更新的信息、更好的步骤、更清晰的描述。如有 → 更新已有 skill。如无 → **跳过。**

3. **skill_search 无结果（不重叠）？**
   - 检查 candidate 是否能**合并到**某个功能相近的已有 skill 中（不仅仅是语义相似，而是看功能覆盖度是否有交集）
   - candidate 的核心功能已被某个已有 skill 部分覆盖，或两者解决的是同一类问题 → 走 **Compare and merge** 流程，将 candidate 的内容合并到已有 skill 中
   - 功能确实全新 → 走下方完整创建流程

4. **完整创建流程**（仅当上述步骤都确认需要新建时才执行）：
   1. **检查 trigger**：确认有明确的触发信号（用户关键词、消息类型、工具返回、cron 周期）。没有外部 trigger 的 skill 不应创建。
   2. **创建目录**：`mkdir -p $WORKSPACE/skills/<name>/`
   3. **写入 SKILL.md** 使用 `write_file(path="$WORKSPACE/skills/<name>/SKILL.md", content="...")`。必须包含 `## When to Use`、`## Steps`、`## Verification` 三个章节。末尾包含自我优化脚注（见 [自我优化脚注](#self-optimization-footer)）。
   4. **验证触发条件（最终确认）**：从 SKILL.md frontmatter 中读取 skill 的 description，然后检查它是否正确出现在 skills 索引中：`exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('$WORKSPACE')).build_skills_summary())")`。确认 description 足够具体，使得匹配的任务到来时你会加载此 skill。如果不是，立即编辑 description。
   5. **验证**：`exec(python {baseDir}/scripts/quick_validate.py $WORKSPACE/skills/<name>)`
   6. 修复任何验证错误

### Patch a skill (targeted fix)
当 skill 的指令有误时：
1. `read_file(path="$WORKSPACE/skills/<name>/SKILL.md")` — 读取当前内容
2. `edit_file(old_string="<wrong text>", new_string="<corrected text>")` — 修复特定部分。
3. `exec(python {baseDir}/scripts/quick_validate.py $WORKSPACE/skills/<name>)` — 验证

### Edit a skill (full rewrite)
1. `read_file(path="$WORKSPACE/skills/<name>/SKILL.md")` — 读取当前内容
2. `write_file(path="$WORKSPACE/skills/<name>/SKILL.md", content="<complete new content>")` — 完全替换。
3. 验证

### Delete a skill
1. 与用户确认
2. `exec(rm -rf $WORKSPACE/skills/<name>)`

### Compare and merge skills
当有新的 skill candidate 与已有 skill 功能重复时，需要对比后决策。这个流程被 assess_me 和 MemoryExtractor 的子 agent 引用：

1. **读两边完整内容**：`read_file("$WORKSPACE/skills/<existing>/SKILL.md")` 和 candidate 的描述
2. **逐维度对比**：

   | 维度 | 新 candidate | 已有 skill |
   |------|-------------|------------|
   | Trigger 是否明确 | 外部信号清晰？ | 已有描述够具体？ |
   | 步骤是否完整 | 覆盖了哪些场景？ | 缺少哪些角度？ |
   | Verification 是否可执行 | 有可验证的成功标准？ | 验证项是否过时？ |
   | 信息是否准确 | 命令/路径/参数正确？ | 有没有过期内容？ |

3. **决策**：
   - 新 candidate 明显更好 → **替换**（write_file 覆盖）
   - 各有价值 → **合并**（读原有内容，整合 Steps/Pitfalls/Verification，write_file 写回）
   - 已有 skill 已完整覆盖 → **跳过**
4. **合并注意事项**：保留两边的 Pitfalls 去重、Verification 取并集、Steps 按顺序整合。**合并后的 name/description 必须覆盖各 skill 原有的触发场景**，确保原来的用户场景不会因改名而丢失触发覆盖。

### Add supporting files
`write_file(path="$WORKSPACE/skills/<name>/references/<filename>.md", content="...")`
`write_file(path="$WORKSPACE/skills/<name>/scripts/<filename>.py", content="...")`

允许的子目录：`scripts/`、`references/`、`assets/`

### List existing skills
`exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('$WORKSPACE')).build_skills_summary())")`

---

## Nanobot Skill Format

每个 skill 是一个包含 `SKILL.md` 文件的目录：

```
$WORKSPACE/skills/<name>/
├── SKILL.md (必填)
├── scripts/      — 可执行代码（可选）
├── references/   — 文档（可选）
└── assets/       — 模板、图片（可选）
```

### SKILL.md Frontmatter

```yaml
---
name: skill-name           # 连字符命名法，小写
category: code-analysis     # 必填。创建前运行 list_categories.py 查看现有 category，选择合适的；如有需要可创建新值
description: >
  [功能概述]。
  当用户[场景1]、[场景2]、[场景3]时，必须使用此 Skill。
  关键词：[关键词1]、[关键词2]、[关键词3]。
  即使用户没有明确说'[精确术语]'，只要涉及[相关概念]，都应触发。
always: false
---
```

**关键**：description **全部是 trigger 信号**，三段式不存在"功能说明"和"触发条件"两个角色——所有部分都服务于同一个目标：让 LLM 在 skills_summary 中匹配到它。

1. **功能** — 主 trigger。从"管什么方面的问题"角度匹配，是 LLM 扫读时的第一筛
2. **场景** — 精确 trigger。用户具体说什么/做什么时激活，结尾必须加"必须使用此 Skill"
3. **关键词 + 隐含触发** — 兜底 trigger。用户没说精确词但涉及相关概念时也能匹配

LLM 在 skills_summary 只看这一行来决定是否 `read_file`。**所有部分都服务于匹配命中，不存在非 trigger 的内容。**

### 核心原则：Trigger → Action → Goal

**没有触发条件的 skill 不应该被创建。**

每个 skill 必须满足：
1. **明确 trigger** — 什么场景下加载？用户说了什么？系统状态是什么？
2. **具体 action** — 触发后按什么步骤做？
3. **可验证的 goal** — 执行后怎么判断成功？在 [## Verification](#verification) 章节写清楚
4. **不依赖 LLM 主动想起来** — trigger 应当来自外部（用户消息、工具结果、cron、hook），而不是"LLM 反省时自动触发"

判断的检验标准：
- trigger 可以被自动检测（用户关键词、消息类型、工具返回）？
- action 是否编号、可执行？
- goal 是否写成了"执行后检查 XX"的形式？
- 还是需要 LLM 在空闲时"回想起来"？→ 后者说明没有真正的 trigger，不适合做 skill

### Good Skill Structure

每个 SKILL.md 必须包含以下章节，**按此顺序**：

```markdown
## When to Use (trigger)
什么场景下加载此 skill。

## Steps (action)
编号步骤，确切命令、代码或流程。

## Verification (goal)
执行后对照此处判断成功还是失败。
格式：可执行的检查项或明确的 success criteria。
例如：
- logcat 中出现了预期的关键字
- gradle build 返回 exit code 0
- 生成的 PNG 文件存在且尺寸正确

## Pitfalls
已知问题、边界情况、操作系统特定说明。
```

还包括：
- **自我优化说明** — 末尾的 self-optimization 脚注不可省略
- **维护说明**：Skill 可自我优化：修复 bug、改进步骤、添加边界情况、增强验证。

### Progressive Disclosure

将 SKILL.md 控制在 500 行以内。将详细内容移至 `references/`：
```
## Quick Start
See [API Reference](references/api.md) for full details.
```

---

## Validation

```bash
python {baseDir}/scripts/quick_validate.py $WORKSPACE/skills/<name>
```

检查项：frontmatter 有效、名称与目录匹配、description 非空、仅使用允许的子目录。

---

## Naming Conventions

| 好 | 差 |
|------|-----|
| `github-pr-workflow` | `github` |
| `pdf-processing` | `pdf` |
| `data-science-pipeline` | `ds` |

- 连字符命名法、小写、仅字母和数字
- 名称暗示 skill 的功能

## What NOT to Include

不要创建：`README.md`、`INSTALLATION_GUIDE.md`、`CHANGELOG.md`。这些文件会让 skill 变得臃肿，对 agent 没有价值。

## Resources

- `scripts/init_skill.py` — 脚手架工具
- `scripts/quick_validate.py` — 结构验证器
- `scripts/list_categories.py` — 列出所有已有的 category（创建 skill 前运行，选择合适的 category）
- `scripts/package_skill.py` — 分发打包工具
- `scripts/ab_test_template.py` — A/B 测试模板
- `references/hermes_triggers.md` — 完整的 Hermes 触发条件参考
- `references/ab_test_reference.md` — A/B 测试执行指南

## Verification

- Skill was created/updated following the correct procedure (Trigger → Action → Goal)
- `quick_validate.py` was run and passed
- For new skills: skill appeared correctly in skills index with specific description
- For patches: the targeted fix addressed the issue without side effects
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
