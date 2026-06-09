---
name: subagent-task
description: Trigger when orchestrating 2+ subagents for parallel work — use for batch spawning with unified verification template, per-batch pivot on failure, cross-agent conflict detection, and result consolidation. Apply when work items are independent and can be parallelized.
---

# Subagent Task Orchestration — Universal

## When to Use

- You need to delegate 2+ subagents to handle independent work items in parallel
- There are 5+ work items that need batched verification
- You need to prevent subagent errors from propagating blindly
- Any task type: code / research / writing / analysis / debugging

**Does not matter what type of task** — any task can be delegated with this framework.

## Steps

### 1. Pre-spawn checks

Before spawning any subagent:

1. **Task has been decomposed** — written to `tasks/<id>.md`, each batch no more than 5 items
2. **Reference files have been read** (for code tasks) — subagents need to know the codebase patterns
3. **Pivot rules are defined** — failure threshold, iteration limit, stop signal

Core principle: **small batch + verify + pivot**. Do not wait for all subagents to finish before verifying. Verify after each batch:
- Pass -> continue to next batch
- Fail -> stop or fix, do not push blindly

### 2. Create subagent task template

Each subagent task must include these sections:

```markdown
## 任务
<Specific goal and scope>

## 交付物
1. **<deliverable type>** — <specific file/result path>
2. **Work report** — written to tasks/<id>.md, including:
   - What was done
   - Results
   - File list
   - Key decisions and rationale

## 边界
- **Not doing**: <explicit list of out-of-scope items>
- **Escalation conditions**: <when to stop and ask, not continue>

## 强制规则
1. **Run <first verification command> first** — if it fails, report immediately
2. **Pivot rules**:
   - <verification passes> -> continue
   - <verification fails> -> fix and rebuild, max 3 attempts per item
   - **Same item fails 3 times** -> stop and report
   - **Batch accumulates 5 iterations** -> stop and report (entire batch stops)
3. **Stop and ask when uncertain** — do not guess decisions, wait for orchestrator reply

## 退出检查
- All files/results delivered to disk
- <verification condition> (BUILD SUCCESSFUL / 验证通过 / etc.)
- Work report written to tasks/<id>.md
- Final response includes work summary (not just "已完成")
```

### 3. Spawn and verify per batch

**Parallelism strategy**:

| Scenario | Strategy |
|---|---|
| 2 subagents, no dependencies | **Parallel** spawn (launch simultaneously) |
| Multiple subagents, with dependencies | **Serial** — first completes -> verify -> next launches |
| Same batch has 3+ items | **Parallel** within subagent, verify per batch |
| A subagent's output has errors | **Cancel** + fix prompt + re-spawn |
| A subagent needs user decision | **Stop**, report to user, wait for reply |

**Verification flow (Orchestrator side)** after each batch:

1. **Check subagent report** — files delivered to disk? Content complete?
2. **Execute verification** — BUILD / read results / run check script
3. **Pivot decision**:
   - Pass -> continue to next batch
   - Repairable -> message subagent to fix
   - Stop -> cancel subagent, write report, ask user

### 4. Handle conflicts and issues

When multiple subagents run in parallel, check for:
- **Same file modified** -> diff check, resolve conflicts before continuing
- **Same resource used** -> merge or queue

**Common issue resolution**:

| Issue | Resolution |
|---|---|
| Subagent reports "done" but no files | Check report content -> cancel + reassign |
| Subagent timeout (100 iterations) | Check progress, cancel + reassign smaller batch |
| Subagent needs user decision | **Stop**, report to user, wait for reply |
| Subagent goes off track | **Cancel**, adjust prompt, re-spawn |
| Multiple subagents modified same file | Merge diffs, resolve conflicts first, then continue |
| Verification fails but subagent says minor | Uphold verification standard, do not skip failures |

### 5. Finalize

**Stop report** (when a batch fails irrecoverably):
```markdown
## 停下报告

**已完成**：
- item A
- item B

**遇到问题**：
- item C — <error description>
- <fix attempted>
- <why it failed>

**建议**：
- <next steps>
- <whether user decision is needed>
```

**Completion report** (when all batches succeed):
```markdown
## 完成报告

**批次**：N
**完成 items**：A, B, C
**未完成 items**：无

**验证**：
- [x] <verification condition 1>
- [x] <verification condition 2>
- [x] All files delivered to disk
- [x] Work report written
```

## Verification

For each batch, verify the following before proceeding:

- [ ] Subagent files are delivered to disk at expected paths
- [ ] Verification command passes (BUILD SUCCESSFUL / test pass / check script OK / etc.)
- [ ] Work report written to `tasks/<id>.md` with: what was done, results, file list, key decisions
- [ ] No unresolved file conflicts between parallel subagents
- [ ] Final response includes a work summary (not just "已完成")
- [ ] If batch failed: stop report written with clear error description and suggested next steps

**Key verification principles**:
- Verify immediately after each batch, not at the end
- Set verification criteria before spawning, not during verification
- Pivot is normal behavior, not failure — stopping in time is better than pushing blindly
- The subagent's final text response is the only delivery artifact — file delivery alone is not completion

## Pitfalls

- **Subagent reports "done" with no files**: The report content may be empty or the subagent hallucinated delivery. Check report, cancel, and reassign with a more explicit output requirement.
- **Subagent exceeds 100 tool call limit**: The batch may be too large or the task too complex. Cancel and re-split into smaller batches (2-3 items max).
- **Parallel subagents modify same file**: Diff conflicts can stall progress. Check for shared file targets before spawning parallel agents; if found, serialize or assign to a single subagent.
- **Verification fails but subagent claims it is minor**: Always uphold verification standards. A skipped verification today becomes a hidden bug tomorrow.
- **Subagent stops to ask for user decision**: Do not guess. Report to user and wait. Guessing produces wrong results silently.
- **Wrong for single subagent tasks**: If 1 subagent suffices, do not use this framework — just do it directly.
- **Wrong for real-time interaction**: Subagent spawn-and-wait cycles take time. Not suitable for interactive/hotfix scenarios.

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
