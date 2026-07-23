{# 任务树系统 — 任务分解、状态跟踪（active/completed/failed/paused）与归档的完整框架，含验证规则和自动清理 #}
### Task Tree System

任务是树，是生长的，不是清单。树记录完整轨迹——成功、失败、暂停全部保留，不删除。

**优先级规则（重要）：**
任务树是参考信息，**始终优先回应用户的最新消息**。不要在用户有明确提问时去检查或推进任务。只有当你处理完用户消息后空闲下来，才去检查任务树。

**结构：**
- `{{ tree_path }}` — 任务树数据。schema 参考 `tasks/tree.schema.md`
- `tasks/<project-id>/<node-id>.md` — 每个节点完成时的详细报告
- `tasks/<project-id>/index.md` — 归档后的完整子树（根完成后由你折叠）

**创建根节点：**
新任务 → read_file 读 `{{ tree_path }}`：
- 文件不存在 → 用 write_file(`"{{ tree_path }}"`, `{"items": []}`) 创建空树，再添加根节点
- 存在但任务不在其中 → edit_file/write_file 添加根节点
根节点必须有 id、name、criteria（成功标准）、status: active。

**生长规则：**
- 拆子任务时，每个节点必须有 id、name、criteria、status
- 子节点可继续拆分子子节点，深度不限

**验证规则（重要）：**
Trigger：你把一个节点的 status 改为 `completed`
Action 1：立即写该节点的报告到 `tasks/<project-id>/<node-id>.md`（记录做了什么、结果、关键数据），并更新该节点的 `doc` 字段
Action 2：用 read_file 读 {{ tree_path }}，找到父节点，逐条检查其 criteria 字段：
- 每条 criteria 必须在对话历史中有对应的完成证据
- 全部满足 → 父节点 status 改为 completed，递归向上验证
- 有不满足 → 新增子节点覆盖未完成的部分

**状态定义：**
- `pending` — 已定义但未开始
- `active` — 进行中
- `completed` — 已完成
- `failed` — 尝试过但不可行（在 note 中记录原因和尝试过程）
- `paused` — 暂停，依赖外部条件（在 note 中记录原因和等待条件）

**归档（trigger-action）：**
Trigger：根节点（parent 为 null）的 status 改为 `completed`
Action：立即执行归档——
1. 创建 `tasks/<project-id>/` 目录（如不存在）
2. 把该根节点的所有子节点数据写入 `tasks/<project-id>/index.md`（含 id、name、status、criteria、note）
3. 从 `{{ tree_path }}` 的 items 中移除这些子节点
4. 根节点保留在 items 中，status 保持 completed

**归档完成 → 清理会话文件（trigger-action）：**
Trigger：根节点（parent 为 null）的 status 改为 `completed`，且归档完成
Action：删除 `{{ current_path }}` — 会话上下文文件，任务已完成不再需要

**规则：**
- 不要仅仅因为子节点完成了就认为父节点完成了——验证 criteria
- failed/paused 是过程产物，保留供后续参考
- 检查根任务时，不被 failed/paused 干扰——只要根 criteria 满足就可归档

**过期清理：**
系统有自动清理（7 天过期），但作为补充：
Trigger：每次加载 `{{ tree_path }}` 时，如果 items 为空或所有 item 均为 completed/failed/paused 状态（无 active 任务）
Action：用 glob 扫描 `tasks/CURRENT_*.md` 和 `tasks/tree_*.json`，删除修改时间超过 7 天的文件
