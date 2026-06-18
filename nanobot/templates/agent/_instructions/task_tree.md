### Task Tree System

任务是树，是生长的，不是清单。树记录完整轨迹——成功、失败、暂停全部保留，不删除。

**结构：**
- `{{ workspace_path }}/tasks/tree.json` — 任务树数据。schema 参考 `tasks/tree.schema.md`
- `tasks/<project-id>/<node-id>.md` — 每个节点完成时的详细报告
- `tasks/<project-id>/index.md` — 归档后的完整子树（根完成后由你折叠）

**创建根节点：**
新任务 → read_file_tool 读 tree.json → 如果不在其中，用 edit_file_tool/write_file_tool 添加根节点。
根节点必须有 id、name、criteria（成功标准）、status: active。

**生长规则：**
- 拆子任务时，每个节点必须有 id、name、criteria、status
- 子节点可继续拆分子子节点，深度不限

**验证规则（重要）：**
Trigger：你把一个节点的 status 改为 `completed`
Action：检查该节点的父节点 criteria 是否全部满足
- 满足 → 父节点 status 改为 `completed`，递归向上验证
- 不满足 → 新增子节点覆盖未完成的部分

**状态定义：**
- `pending` — 已定义但未开始
- `active` — 进行中
- `completed` — 已完成
- `failed` — 尝试过但不可行（在 note 中记录原因和尝试过程）
- `paused` — 暂停，依赖外部条件（在 note 中记录原因和等待条件）

**归档：**
只有根节点（parent 为 null）status 改为 `completed` 后才归档。
归档动作：把该根节点的所有子节点写入 `tasks/<project-id>/index.md`，然后从 tree.json 的 items 中移除这些子节点。根节点保留在 items 中，status 不变。

**规则：**
- 不要仅仅因为子节点完成了就认为父节点完成了——验证 criteria
- failed/paused 是过程产物，保留供后续参考
- 检查根任务时，不被 failed/paused 干扰——只要根 criteria 满足就可归档
