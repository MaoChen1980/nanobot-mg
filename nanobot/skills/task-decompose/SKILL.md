---
name: task-decompose
description: Trigger before multi-subagent orchestration when task is large, boundaries are unclear, or work items exceed 5. Use to decompose into dependency-analyzed, complexity-scored batches with clear exit criteria. Applies when a task needs 2+ subagents or 10+ tool calls.
---

# Task Decomposition — Universal

## When to Use

- You receive a large task (needs 2+ subagents or 10+ tool calls)
- You are unsure how many subagents are needed or how much work each should have
- Task boundaries are unclear and need clarification before execution
- You are tackling a task type for the first time and need a workflow design

**Does not matter what type of task** — any task can be decomposed.

## Steps

### 1. List all work units

Use `read_file_tool` / `glob_tool` / `grep_tool` to gather information. List every item that needs processing:

```
work_units = [
    {"id": "A", "desc": "...", "dep": null},
    {"id": "B", "desc": "...", "dep": "A"},  # B depends on A
    ...
]
```

### 2. Analyze dependencies

| Dependency type | Handling |
|---|---|
| **No dependency** | Can be parallelized |
| **One-way dependency** | Do the depended-on item first, then the dependent one |
| **Circular dependency** | Start with the least-dependent part, then handle the rest; if unsplittable, merge into one subagent |
| **Shared resource dependency** | All operations on the same resource go to the same subagent |

### 3. Group into batches

```
batches = [
    {"batch": 1, "items": ["A", "B"], "can_parallel": true},
    {"batch": 2, "items": ["C"], "dep": ["A"], "can_parallel": false},
    {"batch": 3, "items": ["D", "E"], "dep": ["B", "C"], "can_parallel": true},
]
```

**Grouping rules**:
- Items in the same batch may or may not be parallelizable (annotate `can_parallel`)
- Batches may depend on earlier batches (subsequent batches wait for predecessors)
- Max 5 items per batch
- Max 15 items per subagent (avoid timeout)

### 4. Estimate complexity

Rate each item across 5 dimensions:

| Dimension | Simple (1) | Medium (2) | Complex (3) |
|---|---|---|---|
| Item count | 1-5 | 6-15 | 16+ |
| Dependency complexity | None | One-way chain | Circular / multi-directional |
| Tool diversity needed | 1 tool | 2-3 tools | 4+ tools |
| Result predictability | High | Partially uncertain | Highly uncertain |
| Error recovery difficulty | Easy to locate | Needs several debug steps | Errors are hard to detect |

**Total score 5-7**: Simple -> can parallelize aggressively; batch size 5
**Total score 8-11**: Medium -> 3-4 items per batch, prepare pivot rules
**Total score 12-15**: Complex -> 2-3 items per batch, define explicit pivot rules

### 5. Write decomposition to file

Output to `tasks/<id>.md`:

```markdown
# 任务分解 — <task name>

## 任务概述
<One-line description of the end goal>

## 工作单元

| # | Work item | Description | Batch | Depends on | Complexity |
|---|---|---|---|---|---|
| 1 | ... | ... | 1 | - | Medium |
| 2 | ... | ... | 1 | - | Low |
| 3 | ... | ... | 2 | 1 | High |

## 委派计划

### Batch 1 (parallel, N subagents)
- items: D, E
- Dependencies: none

### Batch 2 (serial, waits for Batch 1)
- items: F
- Dependencies: Batch 1

## Pivot Rules
- Failure threshold: same item fails 3 times
- Iteration limit: 5 per batch
- Stop signal: <specific conditions to stop>

## 验证点
- [ ] Verification criteria after each batch
- [ ] All items completed
- [ ] Final deliverable confirmed
```

## Verification

Before submitting the decomposition for delegation:

- [ ] All work units are listed with no omissions
- [ ] Dependencies are analyzed, no circular dependencies (unless explicitly merged into one subagent)
- [ ] Each batch has ≤ 5 items
- [ ] Each batch has clear completion verification criteria
- [ ] High-risk items are identified and marked "complex"
- [ ] Fallback plan exists for each batch's failure case ("if this batch fails completely, what do we do?")
- [ ] Total items assigned to a single subagent does not exceed 15

**Decomposition quality check**: Each work unit should be **Specific** (clear scope, no ambiguity), **Actionable** (subagent has tools to complete it without waiting for others), **Verifiable** (completion criteria are objectively checkable by the orchestrator).

## Pitfalls

- **Over-decomposition**: Tasks with only 1-2 items do not need splitting. Execute directly instead of designing a workflow.
- **Analysis paralysis with complex dependencies**: Do not try to decompose everything at once. Start with the least-dependent parts, work iteratively.
- **Decomposition costs exceed direct execution time**: For urgent fixes or trivial tasks, skip decomposition and execute directly.
- **Ignoring shared resource dependencies**: All operations touching the same file, database, or service should go to the same subagent to avoid conflicts.
- **Batching items that cannot be verified independently**: If a work unit produces no observable output, merge it with a sibling that does, or add an explicit verification step.
- **Wrong for emergency fixes**: Time spent decomposing could be spent fixing. Use judgment: if the fix takes < 5 minutes, just do it.

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
