---
name: subagent-task
description: Orchestrate any parallel subagent work — decompose task into batches, enforce verification per batch, pivot on failure. Use when spawning 2+ subagents for parallel work.
---

# Subagent Task Orchestration — Universal

## When to Use

- 需要委派 2+ 个 subagent 并行处理独立工作项
- 工作项数量大（5+ items），需要分批验证
- 需要防止 subagent 产出有误却继续执行
- 任何类型的任务：代码 / 研究 / 写作 / 分析 / 调试

**不问任务是什么类型** — 任何任务都可以用这个框架委派。

## Core Principle

**小批次 + 验证 + pivot**：
- 不是等 subagent 全部完成再验证
- 每个批次完成立即验证
- 通过 → 继续下一批
- 失败 → 停下 or 修复，不盲目推进

## 委派前检查

在 spawn 之前：

1. **任务已分解** — 写入 `tasks/<id>.md`，每批不超过 5 items
2. **参考文件已读**（如果是代码类）— subagent 需要知道代码模式
3. **Pivot 规则已明确** — 失败阈值 / 迭代上限 / 停下信号

## Task 模板（通用）

每个 subagent 的 task 必须包含：

```markdown
## 任务
<具体要做什么，清晰描述目标和范围>

## 交付物
1. **<交付类型>** — <具体文件/结果路径>
2. **工作报告** — 写到 tasks/<id>.md，包含：
   - 做了什么
   - 结果如何
   - 文件列表
   - 关键决策和理由

## 边界
- **不做**：<明确列出不做哪些>
- **上报条件**：<什么情况应该停下来问，而不是继续>

## 强制规则
1. **先执行 <第一条验证命令>** — 如果失败立即 report
2. **Pivot 规则**：
   - <验证条件> → 继续
   - <验证未通过> → 修复后重建，最多 3 次/项
   - **同一 item 修复 3 次仍失败** → 停下 report
   - **批次累计迭代达到 5 次** → 停下 report（整批停下）
3. **不确定时停下来问** — 不要猜测决策，等 orchestrator 回复

## 退出检查
- 所有文件/结果已落盘
- <验证条件>（BUILD SUCCESSFUL / 验证通过 / 等等）
- 工作报告已写入 tasks/<id>.md
- final response 包含工作总结（说清楚结果，不只是"已完成"）
```

## 验证流程（Orchestrator 侧）

每次批次完成：

1. **检查 subagent report** — 文件是否落盘，内容是否完整
2. **执行验证** — BUILD / 读取结果 / 运行检查脚本
3. **Pivot 决策**：
   - ✅ 通过 → 继续下一批
   - 🔧 可修复 → 发消息给 subagent 修复
   - 🛑 停下 → cancel subagent，写 report，问用户

## 并行策略

| 场景 | 策略 |
|---|---|
| 2 个 subagent，无依赖 | **并行** spawn（同时启动） |
| 多个 subagent，有依赖 | **串行** — 前一个完成 → 验证 → 下一个启动 |
| 同一批次 3+ items | **并行** 写（subagent 内部），分批验证 |
| 某个 subagent 产出有误 | **cancel** + 修复 prompt + 重 spawn |
| 某个 subagent 需要等待用户决策 | **停下**，向用户 report，等回复再继续 |

## 冲突检测

多个 subagent 并行时检查：

- 是否改了同一个文件 → diff 检查，有则先修复冲突
- 是否用了同一个资源 → 有则合并或排队

## 常见问题处理

| 问题 | 处理方式 |
|---|---|
| subagent 报告"已完成"但没有文件 | 检查 report 是否为空 → cancel + 重分配 |
| subagent 超时（100 iterations） | 检查进度，cancel + 重新分配更小批次 |
| subagent 发现需要用户决策 | **停下**，向用户 report，等回复 |
| subagent 方向走偏 | **cancel**，调整 prompt，重新 spawn |
| 多个 subagent 改了同一文件 | 合并 diff，优先解决冲突再继续 |
| 验证失败但 subagent 认为是小问题 | 坚持验证标准，不放过任何失败 |

## Report 格式

### 停下报告
```markdown
## 停下报告

**已完成**：
- item A ✅
- item B ✅

**遇到问题**：
- item C ❌ — <描述错误>
- <尝试的修复>
- <为什么失败>

**建议**：
- <下一步建议>
- <是否需要用户决策>
```

### 完成报告
```markdown
## 完成报告

**批次**：N
**完成 items**：A, B, C
**未完成 items**：无

**验证**：
- [x] <验证条件 1>
- [x] <验证条件 2>
- [x] 所有文件已落盘
- [x] 工作报告已写
```

## 关键原则

1. **subagent 的 final text response 是唯一交付物** — 文件落盘不算完成，必须有 report
2. **Pivot 是正常行为，不是失败** — 及时停下比盲目推进好
3. **不确定就问** — 等待用户决策比猜测决策好
4. **验证标准要提前定好** — 不要在验证时才临时决定标准
5. **每个批次完成后立即验证** — 不要等到全部完成才发现问题

## 边界

- 不适合 1 个 subagent 就能完成的任务
- 不适合无需 subagent 的任务（应直接执行）
- 不适合实时交互场景（subagent 运行时间较长）

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
