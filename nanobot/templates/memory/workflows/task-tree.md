# Task Tree

任务用文件管理。框架不解析树结构，不维护状态机，不强制流程 — 全由 prompt 驱动。

## 文件结构

```
tasks/
├── TREE.md        # 树索引：所有任务的关系和状态概览
├── <id>.md        # 每个任务一个文件：详情、验收标准、进度
└── lessons.md     # 经验教训
```

## TREE.md 格式

```markdown
# Task Tree

## active
- [task-1] 任务标题
  - [task-1a] 子任务标题
  - [task-1b] 子任务标题

## paused
- [task-2] 暂停的任务

## done
- [task-3] 已完成的任务
```

## 单个任务文件格式 (<id>.md)

```markdown
# 任务标题

## Status
active  ← 状态机：todo → active → done
          (叶子节点可自己完成，根节点需用户 confirm)
          侧向：paused / someday / cancelled / failed

## Description
任务描述和背景。

## Acceptance Criteria
- [ ] 验收条件 1
- [ ] 验收条件 2

## Notes
执行过程中的决策和发现。
```

## 生命周期（纯 prompt 驱动）

- **创建**：写 `<id>.md`，更新 TREE.md
- **执行**：读取任务文件，按验收标准执行
- **调研**：用 `✅ investigate: <type>('<args>')` 标记让框架做前置调研
- **验证**：用 `✅ verify: <type>('<args>')` 标记让框架做独立验证
- **完成**：更新任务状态，写摘要到 notes，更新 TREE.md
- **失败/取消**：更新 status 为 failed/cancelled，记录原因

## Investigate / Verify 标记

放在 LLM 的文本回复中，框架自动检测并执行：

```
✅ investigate: file_exists('path/to/file')
✅ investigate: grep('pattern', 'file')
✅ investigate: exit_zero('command')
✅ investigate: llm('research question')
✅ investigate: agent_loop('complex investigation')

✅ verify: file_exists('path')
✅ verify: grep('expected text', 'file')
✅ verify: exit_zero('test command')
✅ verify: llm('verify correctness')
✅ verify: agent_loop('full verification')
```

框架执行后返回结果，不注入主上下文。
