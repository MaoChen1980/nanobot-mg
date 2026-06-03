# Lessons Learned

## Debugging: Externalize Internal State

**Date:** 2026-06-03
**Context:** MiniMax 2013 error — tool_call/tool_result 配对断裂
**Root cause:** 重复 tool_call_id 跨 turn 出现，`_sanitize_messages` 去重删了 tool result 但保留了 tool_call

### Why it was hard
1. **无法缩小问题窗口**：没有从时间（哪个版本引入的）、空间（哪段代码路径）、代码变动（diff 了什么）三个维度去缩小
2. **看不到内部状态**：消息在 pipeline 里逐层变换（`drop_orphan` → `backfill` → `split_thinking` → `_sanitize_messages` → `_enforce_role_alternation`），但每层处理完后的中间状态不可观测。最终是靠 `PRE_SEND_MSGS` dump 出送到 LLM 前的最后 15 条消息才定位到。

### Rule
调试时**第一时间**把被调试对象的内部状态外化——log、dump、中间快照都可以。不要等猜不动了才加。

"外部化"的方式：
- pipeline 类的处理：在关键变换点加 structured dump，显示入/出状态
- 复杂状态：用 hash 或 summary（消息数、tool_call 数、配对情况）而不是全量输出，减少噪音
- 不可观测 == 不可调试

### 三个维度缩小问题窗口
- **时间**：哪个 commit 引入了问题？git bisect
- **空间**：哪段代码路径触发了？trace 跟踪
- **代码**：diff 了什么？review 变化
