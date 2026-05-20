# #debug Rules

## Retry
- **WHEN** 工具返回错误 → **THEN** 读 stderr 诊断，换方法重试（同方法最多 2 次）
- **WHEN** 同一方法失败 2 次 → **THEN** 必须换策略，不试第 3 次
- **WHEN** 某步骤失败 → **THEN** 只修那一步，不重启整个计划
- **WHEN** debug 困难（工具输出不透明、错误信息模糊）→ **THEN** 用 `diagnose` 工具自动排查
- **WHEN** 工具行为异常/不确定能力 → **THEN** 先 `my(action="check")` 诊断，不猜

## Escalation
- **WHEN** 尝试 2 种不同方案后仍无法解决 → **THEN** `escalate_blocker` 记录已尝试方案，`ask_user`
- **WHEN** 验证失败 → **THEN** 分析失败原因，换方案重试

## Reporting
- **WHEN** 汇报失败/错误 → **THEN** 说清发生了什么、原因、下一步，不过度道歉
