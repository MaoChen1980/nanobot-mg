---
name: debug-root-cause
description: >
  Run this when any tool returns an error, debugging stalls,
  or you're starting a new investigation.
  Replaces random exploration with systematic RCA methodology.
  Choose 1-3 methods from 20 and apply step by step.
---

# Debug Root Cause — Systematic Investigation Methodology

## Action

Write the problem definition + selected method to a temp file, read it
back, then execute the investigation.

1. Define the problem in writing
2. Select 1-3 methods from the 20-method catalog below
3. Write problem + method + plan to a temp file
4. Read it back
5. Execute the plan step by step

## Phase 1: Define the Problem

Write to `/tmp/debug-rca.md`:

```
## Problem
What: <error message / unexpected behavior>
Expected: <what should happen>
Frequency: <always / intermittent / conditions>
Impact: <what broke>
```

Deailed reference: [RCA Methods Reference](references/rca-methods.md)

## Phase 2: Select Methods

Pick 1-3 methods based on your situation:

| Situation | Best Methods |
|-----------|-------------|
| Unknown cause, many variables | Divide & Conquer, Single Variable |
| Regression (used to work) | Rollback, Comparison |
| Intermittent failure | Reproduction, Wait & Observe |
| Error message points somewhere | Reverse Inference, Chain Tracing |
| Complex system, many layers | Layer Stripping, Elimination |
| Data looks wrong | Look Inside, Boundary Testing |
| Need to understand unknown code | Log Injection, Time Travel |
| Can't find the pattern | Outlier Analysis, Hypothesis Testing |

### Method Catalog

**1. 分解法 (Divide & Conquer)** — Split the problem space into halves. Test which half contains the bug. Recurse on the failing half.

**2. 对比法 (Comparison)** — Compare working vs failing case. What differs? Environment, input, config, state, timing?

**3. 回退法 (Rollback)** — Revert to known-good state. Re-apply changes one by one. Which change reintroduces the problem?

**4. 假设法 (Hypothesis Testing)** — "If X is true then Y should happen when I Z." Predict, test, confirm or refute.

**5. 逆推法 (Reverse Inference)** — Start at the failure. Trace backward: what had to be true just before? Before that?

**6. 尝试法 (Trial & Error)** — When the search space is small and each attempt is fast. Rapid iteration.

**7. 透视法 (Look Inside)** — Don't trust the surface. Inspect internal state: logs, dumps, debuggers, intermediate values.

**8. 单变量法 (Single Variable)** — Change exactly one factor between tests. Isolate the variable.

**9. 边界法 (Boundary Testing)** — Test edge values: empty, null, zero, max, min, overflow.

**10. 复现法 (Reproduction)** — Find minimal reliable steps to reproduce. Can't fix what you can't reproduce.

**11. 排除法 (Elimination)** — Disable/remove parts. When the problem goes away, the last removed thing is related.

**12. 置换法 (Substitution)** — Replace suspicious component with known-good one. Does the problem follow the component or stay?

**13. 依赖链追溯 (Chain Tracing)** — Walk the full dependency chain. The bug is often not where the symptom appears.

**14. 日志注入法 (Log Injection)** — Add targeted logging at decision points. What path does execution actually take?

**15. 时间回溯法 (Time Travel)** — What changed right before the problem? Config deploy? Data update? Dependency release?

**16. 静候法 (Wait & Observe)** — For intermittent problems with long cycles. Extend observation.

**17. 分层剥离法 (Layer Stripping)** — Bypass outer layers, test the core directly. Add layers back until failure appears.

**18. 离群分析 (Outlier Analysis)** — What's special about failing cases vs passing ones? Common thread?

**19. 强制失败法 (Force Failure)** — Deliberately induce the failure condition. Verify understanding by making it happen on demand.

**20. 橡皮鸭法 (Rubber Ducking)** — Explain the problem to an imaginary colleague. The act of structuring reveals the answer.

Append to the file:

```
## Method
Selected: <method name>
Rationale: <why this method fits>
Plan: <specific steps>
```

## Phase 3: Execute

After reading the file back, follow the plan:

1. Execute one investigation step
2. Record what you learned
3. Update the file with findings
4. Continue or switch method if stuck

## Pitfalls

- Write the problem BEFORE investigating — vague problem = vague debugging
- Pick 1-3 methods and go deep — method-hopping is panic
- Actively try to disprove your hypothesis, not confirm it
- The symptom is not the cause ("null pointer" is a symptom, not root cause)
- If stuck after 2 methods, your problem definition is probably wrong — redo Phase 1

---

**Source**: https://github.com/MaoChen1980/think-skills
**Self-optimization**: After using this skill, improve it based on what you learned.
