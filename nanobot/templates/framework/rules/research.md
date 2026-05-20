# #research Rules

## Escalation
信息获取按序 escalation：`grep`/`glob`/`recall`/`git_inspect` → `web_search` → `ask_user`

## Limits
- **WHEN** 搜索/研究已超 3 轮仍无产出 → **THEN** 停，基于已知信息行动，标注不确定性

## Communication
- **WHEN** 收到模糊指令 → **THEN** 给出 2-3 种解释选项让用户确认
- **WHEN** 需要信息但不确定 → **THEN** 按 escalation 顺序，前一步无结果才进下一步
