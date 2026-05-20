# #debug Rules

## Root Cause Analysis
- **WHEN** 遇到 bug → **THEN** 先缩小范围（divide and conquer），再深入，不猜测
- **WHEN** 定位到问题点 → **THEN** 追溯 commit 历史，理解引入 bug 时的设计上下文
- **WHEN** 修复前 → **THEN** 问自己：这个 bug 是否源于某个设计决策？修复会破坏那个设计吗？
- **WHEN** 修复产生新问题（按下葫芦浮起瓢）→ **THEN** 回退改动，回到设计层面重新分析，不要继续打补丁

## Retry
- **WHEN** 工具返回错误 → **THEN** 读 stderr 诊断，换方法重试（同方法最多 2 次）
- **WHEN** 同一方法失败 2 次 → **THEN** 必须换策略，不试第 3 次
- **WHEN** 某步骤失败 → **THEN** 只修那一步，不重启整个计划
- **WHEN** debug 困难（工具输出不透明、错误信息模糊）→ **THEN** 用 `diagnose` 工具自动排查
- **WHEN** 工具行为异常/不确定能力 → **THEN** 先 `my(action="check")` 诊断，不猜

## Escalation
- **WHEN** 尝试 2 种不同方案后仍无法解决 → **THEN** 记录已尝试方案，ask_user
- **WHEN** 验证失败 → **THEN** 分析失败原因，换方案重试

## Reporting
- **WHEN** 汇报失败/错误 → **THEN** 说清发生了什么、原因、下一步，不过度道歉
