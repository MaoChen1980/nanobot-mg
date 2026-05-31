---
name: skill-manager
description: Creates, patches, and removes skills by recognizing reusable patterns. Operates through standard file tools — reads and writes SKILL.md files. Use when a skill has incorrect steps, after complex tasks, or when you discover a repeatable workflow.
version: 0.1.0
---

# Skill Manager

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
1. **检查重复**：扫描 `skills_summary`（始终在你的 prompt 中）——如果已有 skill 覆盖此功能，则跳过。
2. **创建目录**：`mkdir -p workspace/skills/<name>/`
3. **写入 SKILL.md** 使用 `write_file(path="workspace/skills/<name>/SKILL.md", content="...")`。在你创建的每个 SKILL.md 末尾包含自我优化脚注（见 [自我优化脚注](#self-optimization-footer)）。
4. **验证触发条件（最终确认）**：从 SKILL.md frontmatter 中读取 skill 的 description，然后检查它是否正确出现在 skills 索引中：`exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('workspace')).build_skills_summary())")`。确认 description 足够具体，使得匹配的任务到来时你会加载此 skill。如果不是，立即编辑 description——这是最后的机会。创建后，description 和 trigger 将被冻结，由 skill-manager 所有。
5. **验证**：`exec(python {baseDir}/scripts/quick_validate.py workspace/skills/<name>)`
6. 修复任何验证错误

### Patch a skill (targeted fix)
当 skill 的指令有误时：
1. `read_file(path="workspace/skills/<name>/SKILL.md")` — 读取当前内容
2. `edit_file(old_string="<wrong text>", new_string="<corrected text>")` — 修复特定部分。**切勿更改 skill 的 description 或 trigger**——这些由 skill-manager 所有。
3. `exec(python {baseDir}/scripts/quick_validate.py workspace/skills/<name>)` — 验证

### Edit a skill (full rewrite)
1. `read_file(path="workspace/skills/<name>/SKILL.md")` — 读取当前内容
2. `write_file(path="workspace/skills/<name>/SKILL.md", content="<complete new content>")` — 完全替换。**精确保留原始 description 和 trigger**——它们由 skill-manager 所有。
3. 验证

### Delete a skill
1. 与用户确认
2. `exec(rm -rf workspace/skills/<name>)`

### Add supporting files
`write_file(path="workspace/skills/<name>/references/<filename>.md", content="...")`
`write_file(path="workspace/skills/<name>/scripts/<filename>.py", content="...")`

允许的子目录：`scripts/`、`references/`、`assets/`

### List existing skills
`exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('workspace')).build_skills_summary())")`

---

## Nanobot Skill Format

每个 skill 是一个包含 `SKILL.md` 文件的目录：

```
workspace/skills/<name>/
├── SKILL.md (必填)
├── scripts/      — 可执行代码（可选）
├── references/   — 文档（可选）
└── assets/       — 模板、图片（可选）
```

### SKILL.md Frontmatter

```yaml
---
name: skill-name           # 连字符命名法，小写
description: >
  Clear explanation of what this skill does and WHEN to use it.
  Include specific scenarios, file types, task types that trigger it.
always: false
---
```

**关键**：`description` 字段是用来决定何时使用该 skill 的依据。使其足够具体。

### Good Skill Structure

Skills 的最佳实践包括：
- **触发条件** — 何时使用此 skill
- **编号步骤** — 确切的命令、代码或流程
- **陷阱部分** — 已知问题、边界情况、操作系统特定说明
- **验证步骤** — 如何确认成功
- **自我优化说明** — 使用后，skill 可自我优化：简化步骤、修复 bug、添加边界情况、改进验证，或重新组织以提高清晰度。Description 和 trigger 不得更改——它们是 skill 的契约，由 skill-manager 所有。
- **维护说明** 在末尾："此 skill 可自我优化：修复 bug、改进步骤、添加边界情况、增强验证。请勿更改 description 或 trigger——它们由 skill-manager 所有。"

### Progressive Disclosure

将 SKILL.md 控制在 500 行以内。将详细内容移至 `references/`：
```
## Quick Start
See [API Reference](references/api.md) for full details.
```

---

## Validation

```bash
python {baseDir}/scripts/quick_validate.py workspace/skills/<name>
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
- 最长 64 个字符

## What NOT to Include

不要创建：`README.md`、`INSTALLATION_GUIDE.md`、`CHANGELOG.md`。这些文件会让 skill 变得臃肿，对 agent 没有价值。

## Resources

- `scripts/init_skill.py` — 脚手架工具
- `scripts/quick_validate.py` — 结构验证器
- `scripts/package_skill.py` — 分发打包工具
- `scripts/ab_test_template.py` — A/B 测试模板
- `references/hermes_triggers.md` — 完整的 Hermes 触发条件参考
- `references/ab_test_reference.md` — A/B 测试执行指南

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
