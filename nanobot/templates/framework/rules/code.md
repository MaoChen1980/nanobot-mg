# #code Rules

## Execution
- **WHEN** 收到简单任务 → **THEN** 直接执行，本轮必须有工具调用或结论
- **WHEN** 收到复杂任务（>3 步或有歧义）→ **THEN** 先给大纲，等确认，再执行
- **WHEN** 缺少必要工具 → **THEN** 找现有工具 → 用 `recipe` 组装 → 自己造
- **WHEN** 操作可逆 → **THEN** 直接执行，附回滚路径
- **WHEN** 多个子任务无依赖 → **THEN** 并行执行
- **WHEN** 需调用多个无依赖工具 → **THEN** 同轮批量发出，不串行

## Information
- **WHEN** 准备编辑/写入文件 → **THEN** 先 `read_file` 确认当前内容

## Verification
- **WHEN** `write_file` → **THEN** `then_check` 链式检查语法，`then_exec` 运行，`then_grep` 验证内容
- **WHEN** 做出确定性陈述 → **THEN** 先查证，不凭记忆
- **WHEN** 验证工具结果 → **THEN** 只看返回内容，不调第二个工具"确认"

## Context
- **WHEN** 多次重复读同一文件 → **THEN** 缓存到 `memory/MEMORY.md`
- **WHEN** 重复输入相同命令 → **THEN** `write_file` 写成脚本
