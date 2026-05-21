# Session Reset After Subtask

## Idea

子任务完成后，调用 `new_session` 清空 context，以清洁状态继续下一个任务。

## 问题

当前 `_run_agent_loop` 持续累积消息历史。越往后 signal/noise 越差：
- 大量 thinking/reasoning blocks 挤占窗口
- 模型在长历史中迷失
- context budget 被稀释

## 方案

```
子任务完成
  → agent 更新 TREE.md（进度标记）
  → agent 更新 CURRENT.md（决策上下文 dump）
  → new_session（context 清空）
  → 下一轮读到 TREE.md + CURRENT.md → 恢复
```

### 关键依赖

- **CURRENT.md 质量**：agent 在 reset 前必须可靠地把本轮的关键上下文写入 CURRENT.md（卡点、决策理由、发现）
- **TREE.md 精度**：每一步完成后准确标记进度，让下轮清楚"下一步做什么"
- **Session 管理**：new_session 后要避免丢失未持久化的状态

### Current vs New 对比

| | 连续模式 | Reset 模式 |
|---|---|---|
| Prompt cache | 命中率高 | 每次重新计算 |
| 隐含上下文 | 自动保留 | 需显式写入 CURRENT.md |
| TREE.md 要求 | 粗略即可 | 必须精确 |
| Signal/noise | 随时间劣化 | 每步重置 |
| 调试 | 一条长历史 | 分散在多个 session |

## 约束

1. CURRENT.md 格式已在 SOUL.md 中定义，但 agent 写得不够结构化
2. Reset 前必须保证 TREE.md + CURRENT.md 已持久化
3. 子任务边界需要清晰定义——什么算"一个子任务完成"

## 状态

Idea — 待实验验证。
