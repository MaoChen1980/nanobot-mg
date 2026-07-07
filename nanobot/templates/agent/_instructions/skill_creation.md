## 任务
根据 assess_me 分析结果创建或更新 SKILL.md 文件。

## 输入

{{ skill_pattern }}

## 步骤

0. **粒度门控** — 先判断 pattern 是场景级别还是操作级别：
   - 场景级别（覆盖完整用例，如"android UI 测试策略"）→ 走下方流程
   - 操作级别（单一操作，如"添加 log"）→ **不要创建 skill**，跳过
   - name 要用场景级别：`android-ui-test` 好，`android-add-log` 差

1. **语义查重** — 用 `skill_search` 检索 `{{ workspace_path }}/skills/`，query 用新 skill 的核心功能描述，`k=6`。对召回结果 `read_file` 读全文

2. **对比决策** — 如有功能重复的已有 skill，参考 `skill-manager` 的对比流程（`read_file` 读 `skills/skill-manager/SKILL.md`），判断是替换/合并/跳过。特别注意：如果 candidate 是已有 skill 的子功能，应合并到已有 skill 中而不是新建

3. **创建或更新** — 无覆盖时 `exec mkdir -p` 创建目录再写 `SKILL.md`，需更新时替换或合并

4. **决定加载策略**：
   - 影响**每个**任务的模式（如"验证工具结果再假设"）→ frontmatter 设置 `always: true`
   - 任务特定模式（如"debug FastAPI 启动"）→ 省略 `always: true`，靠 Available Skills 的 description 触发

5. **Skill 格式**：
   - `name` — 场景级别，连字符命名法，如 `android-ui-test`
   - `description` — 两段式 trigger 格式，不要重复 name：
     ```
     [场景入口 — 说明范围]。
     当用户[场景1]、[场景2]时激活。
     ```
     场景 skill 合并了多个子功能时，触发场景段需列出所有子功能入口。
   - `## When to Use` — 触发场景
   - `## Steps` — 具体可执行步骤
   - `## Verification` — 如何确认动作正确完成
   - `always: true` — 仅用于跨任务的通用行为规则
   - `## Pitfalls` — 边界情况和陷阱
   - 末尾 `**Self-optimization**` 脚注
6. **验证输出** — 创建或更新后 `read_file` 确认 frontmatter 和所有必需段落完整

## 约束

- 每个 skill 覆盖一个场景，场景内的不同子操作在 `## Steps` 下用 `###` 分节组织
- 操作级别的不要单独建 skill，应合并到已有场景 skill 中
- 只创建可复用的模式，不要为一次性问题创建 skill
- 使用 `write_file` 或 `edit_file` 创建/更新
- 不能 spawn 子 agent
- 最多 30 次迭代，保持高效
