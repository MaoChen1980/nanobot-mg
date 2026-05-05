# Goal-Scope-Project 信息地图

> 本文档描述 agent 的信息管理层，包括数据模型和验证逻辑。

---

## 1. Agent 实体关系

```
┌─────────────────────────────────────────────────────────────┐
│  AgentLoop                                                    │
│  - 消息循环，接收用户消息                                    │
│  - 路由到 TaskExecutor 或 AgentRunner                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌─────────────────────┐     ┌─────────────────────────────┐
│  TaskExecutor        │     │  AgentRunner（普通对话）    │
│  - Goal 执行协调      │     │  - 直接执行 iteration 循环   │
│  - subtask 管理      │     │                             │
│  - 调用 AgentRunner  │     │                             │
└──────────┬──────────┘     └─────────────────────────────┘
           │
           │ 调用
           ▼
┌─────────────────────────────────────────────────────────────┐
│  AgentRunner（TaskExecutor 调用执行 subtask）                 │
│  - 执行 iteration 循环                                      │
│  - StructuralConstraintVerifier（tool 执行前检查）           │
└─────────────────────────────────────────────────────────────┘
```

**关键**：
- TaskExecutor 调用 AgentRunner 执行 subtask（调用关系，非并列）
- TaskExecutor 负责"做什么"（what），AgentRunner 负责"怎么做"（how）

---

## 2. 存储 Schema

### 2.1 goal.scope

```json
{
  "structural_constraints": {
    "file_patterns": ["src/agent/*.py"],
    "deny_patterns": ["src/channels/**"],
    "api_blacklist": ["exec", "delete_file"],
    "operation_constraints": ["no_delete"],
    "influential_files": ["config.json", ".env"]
  },
  "description": ["重构消息处理逻辑"],
  "success_criteria": ["新结构功能等价"]
}
```

### 2.2 goal.data

```json
{
  "subtasks": [
    {"id": "s0", "title": "前置验证", "status": "in_progress"},
    {"id": "s1", "title": "读取代码", "status": "todo"}
  ],
  "checkpoints": {},
  "hypothesis_verification": {
    "subtask_id": "s0",
    "files_read": [],
    "assumption": null,
    "verification_attempts": [],
    "verdict": null,
    "root_cause": null,
    "adjustment_direction": null
  }
}
```

---

## 3. StructuralConstraintVerifier

### 3.1 位置与目的

**位置**：`runner_execution.py:_run_tool()`，tool.execute() 之前

**目的**：校验 tool 调用是否符合 structural_constraints，阻止越界操作

### 3.2 被拒绝后的行为

```python
async def _run_tool(self_ref, spec, tool_call, ...):
    # Pre-Execution Verifier 检查
    if spec.goal_id:
        verify_result = await verify_action(spec.goal_id, tool_call.name, tool_call.arguments)
        if not verify_result.approved:
            # 阻止执行，返回错误信息给 LLM
            return {
                "content": f"[BLOCKED] {verify_result.reason}\n允许的操作：read_file, write_file 等",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
            }, {"status": "blocked", "reason": verify_result.reason}, None

    result = await tool.execute(**params)
    return result, {"status": "success"}, None
```

**关键**：
- 不执行 tool，返回 blocked 结果
- LLM 会收到错误，可以换其他 tool 重试
- 不是抛出异常，是返回错误消息

---

## 4. HypothesisVerification（假设验证机制）

### 4.1 核心设计原则

**目的**：获取客观事实，不依赖 LLM 自我报告

**设计**：
1. LLM 声明假设（assumption）
2. 系统执行验证，返回客观结果
3. **系统判定 verdict**（不是 LLM 填写）
4. LLM 根据 verdict 决定下一步

### 4.2 防止欺骗

**问题**：LLM 可能声明错误的 expected 值来绕过验证

**解决方案**：expected 由 LLM 声明，但系统只验证"声明的值 vs 实际的值"，不验证"声明的值是否合理"

```
LLM 声明: expected = "new_name"
系统验证: actual = "old_name"
结果: passed = False（系统判定）

LLM 无法声称 passed = True，因为是系统判定的
```

**局限**：如果 LLM 故意声明错误的 expected，系统无法发现。但这比"LLM 完全自我报告"要好。

### 4.3 强制调用机制

**问题**：LLM 可以跳过 declare_assumption，直接声称 subtask_0 完成

**解决方案**：subtask_0 的 status 必须在 declare_assumption 被调用后才能标记为 done

```python
def _enforce_subtask_0(goal_id: str) -> str | None:
    goal = db.get_goal(goal_id)
    hyp = goal.get('data', {}).get('hypothesis_verification', {})

    # 检查 hypothesis_verification 是否完整
    if not hyp.get('assumption'):
        return "⚠️ subtask_0 未完成：未声明假设"

    if not hyp.get('verification_attempts'):
        return "⚠️ subtask_0 未完成：未执行验证"

    if not hyp.get('verdict'):
        return "⚠️ subtask_0 未完成：未获得验证结论"

    return None
```

---

## 5. structural_constraints 定义

### 5.1 operation_constraints 粒度

```python
# 操作类型定义
OPERATION_TYPES = {
    "read": ["read_file", "grep", "glob", "list_dir"],
    "write": ["write_file", "edit_file"],
    "delete": ["delete_file", "rm"],
    "execute": ["exec", "run_command"],
    "network": ["send_message", "http_request"],
}

# operation_constraints 检查
def check_operation_allowed(tool_name: str, constraints: list[str]) -> bool:
    tool_op = None
    for op, tools in OPERATION_TYPES.items():
        if tool_name in tools:
            tool_op = op
            break

    if tool_op is None:
        return True  # 未知 tool，默认允许

    for constraint in constraints:
        if constraint == "read_only" and tool_op not in ["read"]:
            return False
        if constraint == "no_delete" and tool_op == "delete":
            return False

    return True
```

### 5.2 influential_files 作用

```
influential_files = ["config.json", ".env"]

含义：
1. subtask_0 必须读取这些文件
2. subtask_0 完成后，这些文件的语义（即代码依赖的值）被验证
3. 后续 subtask 如果修改这些文件，需要重新验证
```

---

## 6. subtask_0 完成条件

### 6.1 严格完成条件

```
subtask_0 完成条件（必须同时满足）：

1. files_read 非空
   └─ 必须包含 goal.scope.structural_constraints.influential_files 中的文件

2. assumption 非空
   └─ 通过 declare_assumption 声明

3. verification_attempts 非空
   └─ 系统执行了验证（至少有 1 次验证结果）

4. verdict 非空
   └─ 系统判定（passed 或 failed）
```

### 6.2 防止形式化绕过

```python
def _validate_subtask_0_integrity(goal_id: str) -> bool:
    """
    验证 subtask_0 不是形式化完成
    """
    goal = db.get_goal(goal_id)
    hyp = goal.get('data', {}).get('hypothesis_verification', {})
    scope = goal.get('scope', {}).get('structural_constraints', {})

    # 1. 检查 influential_files 是否被读取
    influential = scope.get('influential_files', [])
    files_read = hyp.get('files_read', [])
    if not all(f in files_read for f in influential):
        return False  # 没有读取所有 influential files

    # 2. 检查 assumption 是否与 influential_files 相关
    assumption = hyp.get('assumption', {})
    if assumption:
        # assumption 应该涉及代码依赖的关键值
        # 至少应该有 claim 描述
        if not assumption.get('claim'):
            return False

    return True
```

---

## 7. 多轮验证机制

### 7.1 追加 vs 覆盖

```python
# declare_assumption 调用时
if hyp.get('assumption'):
    # 已有 assumption，这是新的验证尝试
    # 追加到 verification_attempts
    hyp['verification_attempts'].append({
        "assumption": assumption,
        "result": verification_result,
        "verdict": verdict,
        "timestamp": now()
    })
    # 更新最新 verdict
    hyp['verdict'] = verdict
else:
    # 第一次声明
    hyp['assumption'] = assumption
    hyp['verification_attempts'].append({
        "assumption": assumption,
        "result": verification_result,
        "verdict": verdict,
        "timestamp": now()
    })
    hyp['verdict'] = verdict
```

### 7.2 最大重试次数

```python
MAX_HYPOTHESIS_VERIFICATION_ATTEMPTS = 3

def check_verification_exhausted(goal_id: str) -> bool:
    hyp = db.get_goal(goal_id).get('data', {}).get('hypothesis_verification', {})
    attempts = hyp.get('verification_attempts', [])

    if len(attempts) >= MAX_HYPOTHESIS_VERIFICATION_ATTEMPTS:
        return True

    return False
```

---

## 8. 开口项状态

| # | 功能 | 状态 |
|---|------|------|
| 1 | StructuralConstraintVerifier | 📋 待实现 |
| 2 | HypothesisVerifier | 📋 待实现 |
| 3 | declare_assumption tool | 📋 待实现 |
| 4 | _enforce_subtask_0 | 📋 待实现 |
| 5 | verify_functions.py | 📋 待实现 |

---

## 9. 版本记录

- 2026-05-04: v1.0 初稿
- 2026-05-04: v2.0-v5.0 多轮迭代
- 2026-05-04: v6.0 修复 Pre-Execution Verifier 拒绝行为、subtask_0 强制机制、verdict 系统判定、多轮验证追加规则
- 2026-05-04: v7.0 修复 TaskExecutor 与 AgentRunner 关系图表（调用关系非并列）、HypothesisVerifier 更名为 HypothesisVerification
