# #plan Rules

## Read-First Planning
- **WHEN** 接到一个任务 → **THEN** 先读实际代码，再计划。不做"猜了再试"
- **WHEN** 需要用到一个模块 → **THEN** 先 `read_file` 读源码，不依赖文档或记忆
- **WHEN** 需要了解项目结构 → **THEN** 先 `read_file("project_card.md")` 或 scan_project，再决定从哪开始
- **WHEN** 理解了一个模块后 → **THEN** 用 `read_file` 验证理解是否正确

## Decision
- **WHEN** 有多个可能方案 → **THEN** 从最可能成功那个开始轮流尝试，但每轮先读代码验证假设
- **WHEN** 一个问题有多个联系少的子问题 → **THEN** 先解决部分独立的子问题
- **WHEN** 每次最优选择可得全局最优 → **THEN** 专注当前最优
- **WHEN** 需追踪思考过程 → **THEN** 记录每次思考和行动

## Verification
- **WHEN** 重大决策前 → **THEN** 回顾对话时间线，提炼目标/已做决策/工具链，从零推导对照验证（双重确认）
- **WHEN** 行动前 → **THEN** 确认三件事：该做吗？方法对吗？能高效吗？
- **WHEN** 方案涉及代码改动 → **THEN** 确认已经读过相关代码，不凭训练数据做假设

## Task Lifecycle
见 `read_file("tasks/TREE.md")`
