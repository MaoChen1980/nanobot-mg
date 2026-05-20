# Cross-Turn Collaboration Strategy

遇到大型任务，按任务结构选择协作策略。

## Independent Subtasks

多个项目、多个无关模块 → 用 `spawn` 并行派子代理。每个子代理只探索一个项目，输出中间文件，最后汇总。

## Dependent Subtasks

单一模块，步骤间依赖 → 分多轮完成，每轮持久化中间状态。

## Core Principle

不要在一轮里硬撑。信息量过半但没做完 → 停下来，下轮继续。
