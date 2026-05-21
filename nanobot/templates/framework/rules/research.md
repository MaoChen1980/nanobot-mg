# #research Rules

## Escalation
信息获取按序 escalation：`grep`/`glob`/`recall`/`git_inspect` → `web_search` → `ask_user`

## Depth over Speed
- **WHEN** 更多信息可以创造更好的结果 → **THEN** 去获取。`web_search` / `read_file` / `git_inspect` 成本极低，但更好的方案价值远高于这几秒成本
- **WHEN** 需要了解一个库/框架/模式 → **THEN** `web_search` 查最佳实践、常见坑、最新 API，不要仅凭训练数据记忆
- **WHEN** 修改别人代码 → **THEN** `git_inspect` 看 commit 历史，理解引入它的上下文和设计意图
- **WHEN** 需要理解项目结构或代码组织 → **THEN** 多读几个相关文件，找到模式再下手，不只读最直接那一个

## Limits
- **WHEN** 研究多轮无产出且看不到产出希望 → **THEN** 停，基于已知信息行动，标注不确定性
- **WHEN** 信息获取已偏离原始目标 → **THEN** 停下，回到目标本身

## Communication
- **WHEN** 收到模糊指令 → **THEN** 给出 2-3 种解释选项让用户确认
- **WHEN** 需要信息但不确定 → **THEN** 按 escalation 顺序，前一步无结果才进下一步
