---
name: task-decompose
description: Decompose any large task into subagent-friendly units — assess complexity, identify dependencies, group into batches, design exit criteria. Use before any multi-subagent orchestration.
---

# Task Decomposition — Universal

## When to Use

- 收到任何大型任务（需要 2+ subagent 或 10+ tool calls）
- 不确定该拆成几个 subagent、每个多少工作
- 任务边界不清晰，需要先理清再动手
- 第一次处理某类任务，需要设计工作流

**不问任务是什么类型** — 任何任务都可以分解。

## Core Principle

**分解的目标**：让每个 subagent 拿到的工作单元是 **Specific / Actionable / Verifiable** 的。

| 属性 | 含义 |
|---|---|
| Specific | 范围清晰，不会做多也不会做少 |
| Actionable | subagent 有工具可以完成，不需要等别人 |
| Verifiable | 完成标准明确，orchestrator 可以检查 |

## 分解流程

### Step 1: 列出所有工作单元

用 `read_file_tool` / `glob_tool` / `grep_tool` 等工具收集信息，列出所有需要处理的工作项。

```
工作单元 = [
    {"id": "A", "desc": "...", "dep": null},
    {"id": "B", "desc": "...", "dep": "A"},  # B 依赖 A
    ...
]
```

### Step 2: 分析依赖关系

| 依赖类型 | 处理方式 |
|---|---|
| **无依赖** | 可以并行 |
| **单向依赖** | 先做被依赖的，再做依赖别人的 |
| **循环依赖** | 先做依赖最小的部分，再处理剩余依赖；无法拆分则合并到同一个 subagent |
| **共享资源依赖** | 同一资源的所有操作放同一个 subagent |

### Step 3: 按批次分组

```
批次 = [
    {"batch": 1, "items": ["A", "B"], "can_parallel": true},
    {"batch": 2, "items": ["C"], "dep": ["A"], "can_parallel": false},
    {"batch": 3, "items": ["D", "E"], "dep": ["B", "C"], "can_parallel": true},
]
```

**分组规则**：
- 同一批次内的 items 是否可以并行
- 批次之间是否有依赖（后续批次是否需要等前面完成）
- 每批 items 数不超过 5 个
- 同一 subagent 任务不超过 15 个 items（避免超时）

### Step 4: 估算复杂度

| 维度 | 简单（1） | 中等（2） | 复杂（3） |
|---|---|---|---|
| 工作项数量 | 1-5 | 6-15 | 16+ |
| 依赖复杂度 | 无依赖 | 单向链 | 循环/多向 |
| 所需工具多样性 | 1 种工具 | 2-3 种工具 | 4+ 种工具 |
| 结果可预期性 | 高 | 部分不确定 | 高不确定 |
| 错误恢复难度 | 容易定位 | 需要几步调试 | 错误隐蔽 |

**总分 5-7**：简单 → 可大胆并行
**总分 8-11**：中等 → 每批 3-4 项，准备 pivot
**总分 12-15**：复杂 → 每批 2-3 项，明确 pivot 规则

## 输出格式

分解完成后，写入 `tasks/<id>.md`：

```markdown
# 任务分解 — <task name>

## 任务概述
<一句话描述：最终目标是什么>

## 工作单元

| # | 工作项 | 描述 | 批次 | 依赖 | 复杂度 |
|---|---|---|---|---|---|
| 1 | ... | ... | 1 | - | 中 |
| 2 | ... | ... | 1 | - | 低 |
| 3 | ... | ... | 2 | 1 | 高 |

## 委派计划

### Batch 1（可并行，N 个 subagent）
- items：D, E
- 依赖：无

### Batch 2（串行，需等 Batch 1）
- items：F
- 依赖：Batch 1

## Pivot 规则
- 失败阈值：同一 item 修复 3 次仍失败
- 迭代上限：5 次/批次
- 停下信号：<具体什么情况应该停下>

## 验证点
- [ ] 每批次完成后的验证标准
- [ ] 所有 items 已完成
- [ ] 最终交付物确认
```

## 分解检查清单

在提交委派前确认：

- [ ] 所有工作单元已列出，无遗漏
- [ ] 依赖关系已分析，无循环依赖（除非明确要合并处理）
- [ ] 每批 items 数不超过 5 个
- [ ] 每批有明确的完成验证标准
- [ ] 已知哪些 items 是高风险（标记为"复杂"）
- [ ] 有 fallback 计划（某批完全失败怎么办）

## 何时不该分批

| 情况 | 做法 |
|---|---|
| 任务只有 1-2 个 items | 直接自己做，不委派 |
| 任务简单且确定 | 直接做，不过度设计 |
| 依赖关系极复杂 | 先做依赖最小的部分，不要试图一次性分解全部 |

## 边界

- 不适合 1-2 个 items 的简单任务（应直接执行无需分解）
- 不适合边界已清晰且只需一个 subagent 的任务
- 不适合紧急修复场景（分解耗时 > 直接修复耗时）

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
