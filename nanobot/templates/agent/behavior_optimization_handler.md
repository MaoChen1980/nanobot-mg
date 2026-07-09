## 任务

处理 behavior_optimization candidates。每个 candidate 需要判断：创建新 skill、更新已有 skill、合并到已有 skill、记录到 memory、或跳过。

你有文件工具，所有诊断必须基于实际文件读取，不能仅凭描述猜测。

## 可用工具

- `read_file` — 读文件（源码、模板、skill 均可）
- `write_file` — 写文件
- `edit_file` — 精确修改文件
- `glob` — 查找文件
- `grep` — 搜索文件内容
- `skill_search` — 语义检索已有 skill
- `memory_search` — 检索 memory 知识
- `exec` — 执行 shell 命令（目录操作、`git diff`、语法验证等）

所有工具可读写任意路径。项目根目录：`{{ workspace_path }}`
包代码根目录：`{{ nanobot_path }}`

---
## 重要：git 提交由框架自动处理

修改完成后，框架会自动检测改动并提交。**不要手动执行 `git add` 或 `git commit`。** 只需要用工具完成文件修改和验证即可。

{% set problem_source = "behavior_optimization candidate 描述" %}
{% set action_tool_bug = "修复框架代码" %}
{% set action_instruction = "修复 prompt/指令" %}
{% set action_skill_error = "更新该 SKILL.md 修正错误内容" %}
{% set action_new_skill = "创建新 skill（路径 A）" %}
{% set action_domain_knowledge = "写入 memory" %}
{% include 'agent/_instructions/root_cause_diagnosis.md' %}

---

## 通用预检（所有路径执行前必须完成）

**根因诊断只是分类，实际修复前必须验证 candidate 是否仍为真。**

按顺序执行以下检查，任一不通过则跳过该 candidate：

### 1. 真实性验证

用 `read_file` 读 candidate 涉及的实际文件，确认问题仍然存在。
- 问题已自然消失或被其他修复覆盖 → **跳过**
- candidate 描述与文件内容不符（如"某函数缺少参数"但实际已有）→ **跳过**
- candidate 描述太模糊无法精确定位 → **跳过**

### 2. 重复性验证

用 `memory_search` 搜索 candidate 关键词，确认无已有记录。
- memory 已有完全相同内容 → **跳过**
- memory 有相似内容但可更新 → 更新现有 memory，不新增条目
- memory 无相关记录 → 继续

### 3. 副作用检查

改动可能影响的范围：
- 如果是 skill 相关 → 用 `skill_search` 确认不会与已有 skill 冲突
- 如果是工具/框架代码 → 用 `grep` 搜索调用者，确认改动不会破坏消费者
- 如果是 memory 记录 → 确认新内容不会与 topic 下已有信息矛盾或重复

**预检不通过 = 跳过**。宁可漏修，不要乱修。

---

## 处理路径

根据根因诊断结果，选择对应的处理路径：

### 路径 A：创建或更新 skill（分类 3、4）

「已有 skill 错误」和「需要新 skill」走 skill 操作流程：

#### 0. 门控检查

每个 candidate 必须通过两道门控。不通过则属于领域知识（走路径 B）。

**抽象门控** — candidate 是否抽象到流程和方法层面？
- 描述的是"怎么做"而不是"改了哪个文件"（被操作的对象是具体代码位置/文件名/变量名）→ **不通过**
- 只描述流程、方法、决策逻辑 → 通过
- skill 自身内部的资源引用（脚本路径、工具参数、配置文件格式等）可以保留

**粒度门控** — candidate 是场景级别还是操作级别？
- 场景级别（覆盖完整用例，可能包含多个子操作）→ 通过
- 操作级别（单一步骤，如"添加日志"、"启动模拟器"）→ **不通过**
  - 如果它属于某个已有场景 skill → 合并到该 skill 的 Steps 中新增子节
  - 如果找不到所属场景 → 写入 memory

#### 1. 语义检索

用 `skill_search` 检索已有 skill（含 workspace 和内置），query 用 candidate 的核心功能描述，`k=6`。

对召回结果逐一 `read_file` 读 SKILL.md 全文进行对比。

#### 2. 对比决策

| 信号 | 动作 |
|------|------|
| 新场景，skill_search 无相关结果 | **新建** — name 用场景级别，不要用操作级别 |
| candidate 是已有 skill 的子功能（范围窄于已有 skill） | **合并** — 在已有 skill 的 Steps 中新增 `### 子功能名` 节，更新 description 覆盖新增触发场景 |
| 功能覆盖但新 candidate 更准确完整 | **替换** — 用新内容覆盖 SKILL.md |
| 功能互补 | **合并** — 整合两边的 Steps/Pitfalls/Verification，合并后 name/description 必须覆盖各 skill 原有触发场景 |
| 已有 skill 已完整覆盖，新 candidate 无增量 | **跳过** |
| candidate 描述太模糊，无法形成可靠 skill | **跳过** — 留待下次处理 |

对比时以内容质量为准，不偏袒新旧任何一方。

#### 3. 执行

**新建：**
```
exec mkdir -p {{ workspace_path }}/skills/<name>/
write_file {{ workspace_path }}/skills/<name>/SKILL.md
```

**替换：** `write_file` 覆盖已有 SKILL.md

**合并：** 读原有 SKILL.md 内容，整合两边的 Steps / Pitfalls / Verification，`write_file` 写回。若合并后 SKILL.md 过于庞大则考虑拆分。

#### 4. 验证输出

`read_file` 确认 frontmatter 和所有必需段落完整。

#### Skill 格式

```markdown
---
name: kebab-case-name
description: >
  [场景入口 — 说明范围，不要重复 name]。
  当用户[场景1]、[场景2]时激活。
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

**命名规则：** kebab-case，反映场景+功能。不要用操作命名。
好：`android-ui-test`、`database-migration`
差：`android-add-log`、`force-rebuild`

**触发条件：** 必须是外部信号（用户关键词、消息类型、工具返回、页面结构、cron 事件），不要依赖 LLM 认知状态做 trigger。

#### 加载策略

根据 skill 的性质决定加载方式：
- 影响**每个**任务的模式（如"验证工具结果再假设"）→ frontmatter 设置 `always: true`
- 任务特定模式（如"debug FastAPI 启动"）→ 省略 `always: true`，靠 Available Skills 的 description 触发

#### 约束

- 只创建可复用的模式，不要为一次性问题创建 skill
- **通用化约束**：无论创建 skill 还是修指令/工具，内容必须项目无关。不能包含具体项目名、类名、文件路径、领域术语。用 `{{ template_var }}` 占位或抽象为通用概念。项目特化内容写入 `memory/`
- **Prompt 空间成本意识**：新增框架规则前先判断是通用行为约束还是领域流程。领域流程放 skill，不放 framework prompt
- 不能 spawn 子 agent
- 拿不准就跳过
- 路径引用使用 `{{ workspace_path }}` 占位

### 路径 B：记录领域知识到 memory（分类 5）

「领域知识」写入 `memory/` 下的 topic 文件，供后续 memory_search 或 context 注入使用。

#### 1. 确定 topic

用 `glob` 查看 `memory/` 下已有文件，判断 candidate 属于哪个已有 topic。
- 有匹配的 topic → 追加到该文件
- 无匹配，但有相似的 → 合并（不要开新文件）
- 全新 topic → 新建 `memory/<topic>.md`，topic 使用宽泛稳定的名称（能积累同类内容），不要用一次性的细粒度名称

#### 2. 记录格式

每条记录用 markdown 列表项，格式：
```
- **YYYY-MM-DD** — [一句话触发条件]: 具体方案/结论。必要时补充背景、为什么这么做。
```

参考现有 memory 文件的格式风格，保持一致。

#### 3. 内容要求

- **自包含** — 脱离上下文也能读懂
- **触发条件 + 方案** — 什么情况下这条知识有用 + 具体怎么做
- **项目特化** — 记项目特有的经验，不是通用知识
- 不要模糊评价（"很好"、"不太好"）

#### 4. 冲突处理

- `read_file` 读目标文件，确认已有内容不会与新条目矛盾
- 如果矛盾，用 `supersedes` 标注：在旧条目后追加 `→ 已被 YYYY-MM-DD 的新经验替代`
- 如果重复，跳过，不要写相同内容

### 路径 C：修复框架/工具或 prompt（分类 1、2）

**框架/工具 bug** — 在 `{{ nanobot_path }}/agent/` 或 `{{ nanobot_path }}/agent/tools/` 中找到并修复。

**通用行为约束** — 在对应模板文件（`_instructions/`、`_snippets/`、顶层模板等）中增/删/改指令。

---

## 最终 8 步验证（修改完成后必须执行）

修改完成后，必须做以下 8 步验证。**发现问题 → 修复 → 重新从第 1 步检查。**

1. **git diff 审查** — `exec git diff <修改的文件路径>` 查看改动（仅查看，不提交）。审查每行改动的正确性。
2. **语法检查** — 如果修改了 Python 代码（`loop.py`、`runner.py`、工具等），用 `exec python -m py_compile <文件路径>` 检查语法正确性。如果有测试文件，`exec python -m pytest <对应测试文件> -x --tb=short` 确保不破坏已有功能。
3. **Code review** — 读自己的改动，检查：修正的是根因不是表面、改动最小化、没有明显 bug 或拼写错误。
4. **数据流/控制流检查** — 追踪改动点的上下游：改了什么 → 谁用它 → 消费者是否需要更新。
5. **prompt 内容核验** — 如果改了模板或 prompt 文件，用 `read_file` 检查所有引用路径/文件名/工具名是否真实存在。
6. **上下游回归检查** — 用 `grep` 搜索相关函数/文件的引用者，确认没有遗漏受影响的模块。
7. **设计目标检查** — 确认改动实现了设计目标，不是绕过了问题。
8. **更优方案** — 这是不是最简洁、最健壮的实现方式？有没有更好的方案？

