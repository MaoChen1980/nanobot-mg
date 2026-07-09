## 任务

处理 assess_me 评估发现的问题。根据根因诊断结果走不同分支：修工具 bug、补指令缺陷、更新已有 skill、或创建新 skill。

你有文件工具，所有诊断必须基于实际文件读取，不能仅凭描述猜测。

## 可用工具

- `read_file` — 读文件（源码、模板、skill 均可）
- `write_file` — 写文件
- `edit_file` — 精确修改文件
- `glob` — 查找文件
- `grep` — 搜索文件内容
- `skill_search` — 语义检索已有 skill
- `exec` — 执行 shell 命令（目录操作、`git diff`、语法验证等）

所有工具可读写任意路径。项目根目录：`{{ workspace_path }}`
包代码根目录：`{{ nanobot_path }}`

---
## 重要：git 提交由框架自动处理

修改完成后，框架会自动检测改动并提交。**不要手动执行 `git add` 或 `git commit`。** 只需要用工具完成文件修改和验证即可。

---
## 假阳性记录

如果 Step 0 诊断后发现 assess_me 分类有误（假阳性），必须记录到 `{{ workspace_path }}/memory/system/false_positives.md`：

```markdown
- **日期**: YYYY-MM-DD
  **原始问题**: [assess_me 描述的问题]
  **实际**: [为什么不是需要修的问题]
  **涉及文件**: [相关文件路径]
```

先用 `exec mkdir -p {{ workspace_path }}/memory/system/` 确保目录存在。如果 `false_positives.md` 已存在则追加条目，不存在则创建。

{% set problem_source = "assess_me 的描述" %}
{% set action_tool_bug = "走「分支 A：工具 bug」" %}
{% set action_instruction = "走「分支 B：指令缺陷」" %}
{% set action_skill_error = "走「分支 C：skill 错误」" %}
{% set action_new_skill = "走「分支 D：新 skill」" %}
{% include '_instructions/root_cause_diagnosis.md' %}

---

## 分支 A：工具 bug

问题出在框架工具代码本身，要修代码，不要创建 skill。

### 步骤

1. **读源码确认** — 用 `read_file` 读确认 bug 位置。如果读完后发现不是 bug，说明诊断有误，按「假阳性记录」格式写入 `false_positives.md` 并停止。

2. **修复** — `edit_file` 修复 bug

3. **验证语法** — `exec python -c "compile(open('path').read(), 'path', 'exec')"`

---

## 分支 B：指令缺陷

问题出在框架指令/模板缺少规则，要补指令，不要创建 skill。

### 步骤

1. **读文件确认** — 用 `read_file` 读该指令文件，确认该指令块中确实缺少相关规则或存在错误规则。

   指令模板分布在三个位置：
   - 框架指令块：`{{ nanobot_path }}/templates/agent/_instructions/`
   - 系统 prompt 模板：`{{ nanobot_path }}/templates/agent/system_prompt.md`、`identity.md` 及 `{{ nanobot_path }}/templates/agent/` 目录
   - **运行时渲染快照**：`{{ workspace_path }}/prompts/` 下的 `.pt` 文件

   如果读完后发现相关指令已有对应规则，说明诊断有误，按「假阳性记录」格式写入 `false_positives.md` 并停止。

2. **修复** — `edit_file` 以 trigger-action 格式增/删/改规则。目标是修复错误，增删改都是手段。

3. **通用化检查** — 修改的规则必须满足：不包含具体项目名、类名、文件路径、领域术语。如果新规则包含这些，必须抽象为通用概念（用 `{{ template_var }}` 占位或改用"如 XXX 则 YYY"形式）。项目特化内容应写入 `memory/` 而非框架指令或 skill。

4. **验证** — `read_file` 确认改动正确、格式一致、通过通用化检查

---

## 分支 C：skill 错误

已有 SKILL.md 指引错误，要更新该 skill，不要创建新 skill。

### 步骤

1. **读 SKILL.md 确认** — 用 `read_file` 读全文。如果指南与 assess_me 描述的不一致，按「假阳性记录」格式写入 `false_positives.md` 并停止。

2. **对比分析** — 结合 assess_me 的分析，确定错误在哪

3. **修复** — `edit_file` 修正 Steps / Pitfalls / Verification 中的错误内容

4. **验证** — `read_file` 确认 SKILL.md 格式完整

---

## 分支 D：新 skill

真正需要创建新 skill。

### 步骤

0. **抽象门控 + 粒度门控** — 两步过滤：
   - **抽象门控**（先）：skill 必须抽象到**流程和方法层面**。描述的是"怎么做"而不是"改了哪个文件"——被操作的对象（用户代码库的文件路径、行号、变量名等）不能出现在 Steps/Pitfalls 中；但 skill 自身内部的资源引用（脚本路径、配置文件格式、工具参数等）可以保留。如果写的内容脱离当前上下文就不可用，说明不够抽象。
   - **粒度门控**（后）：场景级别（覆盖完整用例）→ 走下方流程；操作级别（单一操作）→ **不要创建 skill**，跳过

1. **语义查重** — 用 `skill_search` 检索 `{{ workspace_path }}/skills/`，query 用核心功能描述，`k=6`。对有相似结果的 `read_file` 读全文

2. **对比决策** — 如有功能重复的已有 skill：替换（新的更好）/ 合并（互补）/ 跳过（已覆盖）

3. **创建或更新** — 无覆盖时 `exec mkdir -p {{ workspace_path }}/skills/<name>/` → `write_file` 写 SKILL.md

   **格式要求：**
   ```markdown
   ---
   name: kebab-case-name
   description: >
     [场景入口]。
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

4. **验证输出** — `read_file` 确认 frontmatter 和所有必需段落完整

---

## 通用约束

- 不要为一次性问题创建 skill
- skill 抽象到流程和方法层面：描述"怎么做"而非"改了哪个文件"。被操作的对象不能写具体路径/行号，但 skill 自身内部的资源引用（脚本路径、工具参数等）可以保留
- **通用化约束**：无论修工具代码、补指令规则、还是创建 skill，内容必须是项目无关的通用行为约束。不能包含具体项目名、类名、文件路径、领域术语。项目特化内容写入 `memory/`，而非框架模板或 skill
- 不能 spawn 子 agent
- 拿不准就跳过
- 修改后确保相关代码/文件有效
- Step 0 诊断确定的分支确定后，不要在其他分支上操作

---

## 最终 7 步验证（无论哪个分支，修改完成后必须执行）

修改完成后（修复 bug、补充指令、更新 skill、或创建新 skill），必须做以下 7 步验证。**发现问题 → 修复 → 重新从第 1 步检查。**

1. **git diff 审查** — `exec git diff <修改的文件路径>` 查看改动（仅查看，不提交）。审查每行改动的正确性。
2. **Code review** — 读自己的改动，检查：修正的是根因不是表面、改动最小化、没有明显 bug 或拼写错误。
3. **数据流/控制流检查** — 追踪改动点的上下游：改了什么 → 谁用它 → 消费者是否需要更新。
4. **prompt 内容核验** — 如果改了模板或 prompt 文件，用 `read_file` 检查所有引用路径/文件名/工具名是否真实存在。
5. **上下游回归检查** — 用 `grep` 搜索相关函数/文件的引用者，确认没有遗漏受影响的模块。
6. **设计目标检查** — 确认改动实现了设计目标，不是绕过了问题。
7. **更优方案** — 这是不是最简洁、最健壮的实现方式？有没有更好的方案？
