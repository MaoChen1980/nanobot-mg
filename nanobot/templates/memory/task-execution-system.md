# Nanobot 任务系统设计

> 本文档描述 nanobot 的任务执行层设计。

---

## 1. 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│  AgentLoop                                                    │
│  - 消息循环，接收用户消息                                    │
│  - 路由到 TaskExecutor 或 AgentRunner                        │
└──────────┬──────────────────────────────────────────────────┘
           │
           ├──────────────────┐
           ▼                  ▼
┌─────────────────────┐     ┌─────────────────────────────┐
│  TaskExecutor        │     │  AgentRunner（普通对话）    │
│  - Goal 执行协调      │     │  - 直接执行 iteration 循环   │
│  - subtask 管理      │     │                             │
│  - checkpoint        │     └─────────────────────────────┘
└─────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  AgentRunner（TaskExecutor 调用）                             │
│  - 执行 iteration 循环                                      │
│  - StructuralConstraintVerifier（tool 执行前）                │
└─────────────────────────────────────────────────────────────┘
```

**关键**：
- TaskExecutor 和 AgentRunner 是**并列**的
- TaskExecutor 调用 AgentRunner 执行 subtask
- 不是嵌套关系，是调用关系

---

## 2. TaskExecutor vs AgentRunner 职责边界

| 职责 | TaskExecutor | AgentRunner |
|------|--------------|-------------|
| subtask 切换决策 | ✅ | ❌ |
| checkpoint 保存 | ✅ | ❌ |
| subtask_0 强制检查 | ✅ | ❌ |
| goal 完成判定 | ✅ | ❌ |
| iteration 循环执行 | ❌ | ✅ |
| StructuralConstraintVerifier | ❌ | ✅ |
| 普通对话执行 | ❌ | ✅ |

**边界清晰**：
- TaskExecutor 负责"做什么"（what）
- AgentRunner 负责"怎么做"（how）
- AgentRunner 不做业务决策，只执行

---

## 3. subtask_0 强制执行机制

### 3.1 执行位置

**subtask_0 检查在 TaskExecutor 层面，不在 AgentRunner 内部**

```python
class TaskExecutor:
    async def execute_goal(self, goal_id: str, ...):
        goal = self.db.get_goal(goal_id)

        # Step 1: subtask_0 检查（TaskExecutor 层面）
        blocker = self._enforce_subtask_0(goal_id)
        if blocker:
            self.db.update_goal_status(goal_id, 'blocked')
            return GoalExecutionResult(status='blocked', message=blocker)

        # Step 2: subtask_0 完成后，才调用 AgentRunner
        result = await self._execute_subtask(goal_id, subtask_s0, ...)
```

**关键**：LLM 在 subtask_0 完成**之前**，无法调用后续 subtask 的 tools。

### 3.2 _enforce_subtask_0 实现

```python
def _enforce_subtask_0(self, goal_id: str) -> str | None:
    """
    检查 subtask_0 是否完成。
    返回 None 表示通过，返回 blocker message 表示阻止。
    """
    goal = self.db.get_goal(goal_id)
    data = goal.get('data') or {}
    hyp = data.get('hypothesis_verification', {})
    scope = goal.get('scope', {}).get('structural_constraints', {})

    # 1. 检查 influential_files 是否被读取
    influential = scope.get('influential_files', [])
    files_read = hyp.get('files_read', [])
    if not all(f in files_read for f in influential):
        missing = set(influential) - set(files_read)
        return f"⚠️ subtask_0 未完成：未读取 {missing}"

    # 2. 检查是否声明假设
    if not hyp.get('assumption'):
        return "⚠️ subtask_0 未完成：未声明假设（调用 declare_assumption）"

    # 3. 检查是否执行验证
    attempts = hyp.get('verification_attempts', [])
    if not attempts:
        return "⚠️ subtask_0 未完成：未执行验证"

    # 4. 检查是否有 verdict
    if not hyp.get('verdict'):
        return "⚠️ subtask_0 未完成：未获得验证结论"

    return None  # 通过
```

---

## 4. Iteration 边界定义

### 4.1 边界

| 概念 | 定义 |
|------|------|
| **1 iteration** | 1 次 LLM call |
| **1 tool call** | 1 次 tool 执行 |
| **1 subtask** | 多个 iteration，达成一个逻辑目标 |
| **1 goal** | 多个 subtask，达成一个业务目标 |

```
1 goal = [subtask_s0, subtask_s1, ..., subtask_sN]
1 subtask = [iteration_1, iteration_2, ..., iteration_M]
1 iteration = LLM call + 0 或多个 tool calls
```

### 4.2 max_iterations 作用

- 作用：防止单个 goal 占用过长时间
- 触发：iteration 计数达到 max_iterations 时，暂停 subtask，保存 checkpoint
- 与 context window 无关

---

## 5. Progress Block 计数

### 5.1 计数规则

```
Progress = 已完成 subtask 数 / 总 subtask 数

示例：
subtasks: [s0:done, s1:done, s2:in_progress, s3:todo, ...]
Progress: 2/N 完成（N = 总 subtask 数）
```

### 5.2 subtask_s0 的特殊处理

```
如果 s0.verdict = "failed"：
  - s0 标记为 done（验证本身完成了）
  - 但 goal 不能继续（blocked）
  - Progress 仍为 0/N（没有实质性进展）
```

---

## 6. declare_checkpoint 校验

### 6.1 设计

```python
def declare_checkpoint(self, goal_id, subtask_id, summary, artifacts=None):
    """
    允许 LLM 声明 subtask 完成
    """
    goal = self.db.get_goal(goal_id)
    subtasks = goal.get('data', {}).get('subtasks', [])
    current = self._get_current_subtask(subtasks)

    # 允许提前声明（LLM 认为当前 subtask 已完成）
    # 不强制校验 subtask_id == current，因为 LLM 可能确实提前完成了
    if current and subtask_id != current['id']:
        # 警告但不阻止
        warning = f"注意：当前 subtask 是 {current['id']}，你声明的是 {subtask_id}"

    # 更新 subtask 状态
    self._mark_subtask_done(goal_id, subtask_id)
    self._save_checkpoint(goal_id, subtask_id, summary, artifacts)

    return {"status": "ok", "warning": warning if warning else None}
```

**关键**：不阻止提前声明，允许 LLM 自行判断。

---

## 7. Checkpoint 机制

### 7.1 触发条件与状态映射

| 触发条件 | checkpoint 保存 | goal status | 说明 |
|----------|----------------|-------------|------|
| subtask_0 失败 | ✅ | blocked | hypothesis 验证失败 |
| max_iterations 到达 | ✅ | in_progress | 资源配额用尽 |
| context 满 | ✅ | in_progress | context window 限制 |
| 用户 /stop | ✅ | paused | 用户主动暂停 |
| subtask 完成 | ✅ | in_progress | 正常推进 |

### 7.2 blocked vs paused 语义

| 状态 | 语义 | 恢复方式 |
|------|------|----------|
| **blocked** | hypothesis 验证失败，需要调整方向 | /resume_goal + subtask_0 重验 |
| **paused** | 用户主动暂停，可随时继续 | /resume_goal |

### 7.3 恢复流程

```
用户：/resume_goal g1
    ↓
TaskExecutor.resume_goal(goal_id)
    ↓
检查 goal.status
    │
    ├── blocked → _enforce_subtask_0() 重新检查
    │              └─ 通过 → in_progress
    │              └─ 不通过 → 仍 blocked
    │
    └── paused → 继续执行
        └─ 从 last_checkpoint 恢复
```

---

## 8. 聊天与 Goal 执行并存

### 8.1 设计

**Goal 执行期间，用户消息通过 pending queue 注入，不中断 goal**

### 8.2 消息处理

| 消息 | 处理 |
|------|------|
| `/stop` | 暂停 goal，status=paused |
| `/resume_goal` | 恢复 goal |
| 普通聊天 | 进入 pending queue |
| 新 goal 请求 | 进入 pending queue |

### 8.3 pending queue 处理时机

```
Goal 完成 或 用户明确要求处理时：
    ↓
处理 pending queue
    ↓
按顺序处理消息
```

---

## 9. Goal 完成条件

### 9.1 完成判定

```python
def _is_goal_complete(self, goal) -> bool:
    subtasks = goal.get('data', {}).get('subtasks', [])

    # 1. 所有 subtask 完成
    all_done = all(s.get('status') == 'done' for s in subtasks)
    if not all_done:
        return False

    # 2. s0 的 hypothesis 验证通过（不是 failed）
    hyp = goal.get('data', {}).get('hypothesis_verification', {})
    if hyp.get('verdict') == 'failed':
        # s0 验证失败，goal blocked，不能算完成
        return False

    return True
```

### 9.2 失败 vs 完成

| 情况 | 结果 |
|------|------|
| 所有 subtask done + s0 verdict=passed | completed |
| 所有 subtask done + s0 verdict=failed | blocked |
| 部分 subtask done | partial |

---

## 10. Status 状态机

```
┌─────────────┐
│   created   │
└──────┬──────┘
       │ execute_goal
       ▼
┌─────────────┐     s0 hypothesis 失败    ┌──────────┐
│ in_progress │ ────────────────────────▶ │ blocked  │
└──────┬──────┘                           └────┬─────┘
       │                                        │
       │ 所有 subtask 完成 + s0 passed    │ /resume_goal + s0 重验通过
       ▼                                        │
┌─────────────┐                                 │
│ completed  │ ◀────────────────────────────────┘
└─────────────┘

┌─────────────┐
│   paused    │ ◀── 用户 /stop
└──────┬──────┘
       │ 用户 /resume_goal
       ▼
┌─────────────┐
│ in_progress │
└─────────────┘
```

---

## 11. TaskExecutor 完整流程

```python
class TaskExecutor:
    async def execute_goal(self, goal_id: str, max_iterations: int = 50):
        goal = self.db.get_goal(goal_id)

        # === Phase 1: 初始化 ===
        if goal['status'] == 'completed':
            return GoalExecutionResult(status='already_completed')
        if goal['status'] == 'blocked' and not self._can_resume(goal):
            return GoalExecutionResult(status='blocked', message='需要先解决阻塞')

        # === Phase 2: subtask_0 强制检查 ===
        blocker = self._enforce_subtask_0(goal_id)
        if blocker:
            self.db.update_goal_status(goal_id, 'blocked')
            return GoalExecutionResult(status='blocked', message=blocker)

        self.db.update_goal_status(goal_id, 'in_progress')

        # === Phase 3: 循环执行 subtasks ===
        while not self._is_goal_complete(goal):
            current = self._get_next_subtask(goal)

            # 调用 AgentRunner 执行 subtask
            result = await self._execute_subtask(
                goal_id=goal_id,
                subtask=current,
                max_iterations=max_iterations,
            )

            # 更新 subtask 状态
            if self._check_subtask_done(result, current):
                self._mark_subtask_done(goal_id, current['id'])

            # 保存 checkpoint
            self._save_checkpoint(goal_id, current['id'], result)

            # 检查是否需要暂停
            if result.stop_reason in ('context_full', 'interrupted'):
                return self._build_result(goal_id, status='paused')

            if result.stop_reason == 'max_iterations':
                # iteration 配额用尽，暂停
                return self._build_result(goal_id, status='in_progress')

            # 检查 hypothesis 是否失败
            hyp = self._get_latest_hypothesis_verification(goal_id)
            if hyp and hyp.get('verdict') == 'failed':
                self.db.update_goal_status(goal_id, 'blocked')
                return GoalExecutionResult(status='blocked', message='Hypothesis 验证失败')

        # === Phase 4: 完成 ===
        self.db.update_goal_status(goal_id, 'completed')
        return self._build_result(goal_id, status='completed')
```

---

## 12. 开口项状态

| # | 功能 | 状态 |
|---|------|------|
| 1 | TaskExecutor | 📋 待实现 |
| 2 | _enforce_subtask_0 | 📋 待实现 |
| 3 | declare_checkpoint | 📋 待实现 |
| 4 | declare_assumption | 📋 待实现 |
| 5 | StructuralConstraintVerifier | 📋 待实现 |
| 6 | HypothesisVerifier | 📋 待实现 |
| 7 | verify_functions.py | 📋 待实现 |
| 8 | pending queue 处理 | 📋 待实现 |
| 9 | checkpoint 恢复 | 📋 待实现 |

---

## 13. 版本记录

- 2026-05-04: v1.0 初稿
- 2026-05-04: v2.0-v5.0 多轮迭代
- 2026-05-04: v6.0 修复 subtask_0 强制执行位置、职责边界、blocked vs paused 语义、declare_checkpoint 不阻止提前声明
- 2026-05-04: v7.0 与 goal-scope-project-map.md v7.0 同步：确认调用关系图表、StructuralConstraintVerifier 位置明确
