## 任务
根据 assess_me 分析结果创建或更新 SKILL.md 文件。

## 输入

{{ assess_result }}

## 步骤

1. **查重** — 用 `glob` 或 `read_file` 检查 `{{ workspace_path }}/skills/` 下已有 skill。如果已有 skill 已覆盖，评估是否需要更新
2. **创建或更新** — 无覆盖时创建新 `SKILL.md`，不完整时更新
3. **决定加载策略**：
   - 影响**每个**任务的模式（如"验证工具结果再假设"）→ frontmatter 设置 `always: true`
   - 任务特定模式（如"debug FastAPI 启动"）→ 省略 `always: true`，靠 Available Skills 的 description 触发
4. **Skill 格式**：
   - `name` — 简短、描述性
   - `description`（frontmatter 1-2 句）— 触发条件
   - `## Action` — 具体可执行步骤
   - `## Verification` — 如何确认动作正确完成
   - `always: true` — 仅用于跨任务的通用行为规则
5. **验证输出** — 创建或更新后 `read_file` 确认 frontmatter 和所有必需段落完整

## 约束

- 每个 skill 专注一个模式，不要合并
- 只创建可复用的模式，不要为一次性问题创建 skill
- 使用 `write_file` 或 `edit_file` 创建/更新
- 不能 spawn 子 agent
- 最多 10 次迭代，保持高效
