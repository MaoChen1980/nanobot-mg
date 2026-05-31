---
name: cron
description: Schedules reminders, recurring tasks, and one-shot notifications at specific times or intervals. Operates through the agent's cron system — supports cron expressions and ISO datetimes. Use when the user asks to be reminded, set a recurring task, or schedule something.
version: 0.1.0
---

# Cron, tools from system

使用 `cron` 工具安排提醒或重复任务。

## Three Modes

1. **Reminder** - 消息直接发送给用户
2. **Task** - 消息是任务描述，agent 执行并发送结果
3. **One-time** - 在特定时间运行一次，然后自动删除

## Examples

固定提醒：
```
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

动态任务（agent 每次执行）：
```
cron(action="add", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
```

一次性定时任务（根据当前时间计算 ISO 时间）：
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

时区感知 cron：
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

列出/删除：
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

| 用户说 | 参数 |
|-----------|------------|
| 每 20 分钟 | every_seconds: 1200 |
| 每小时 | every_seconds: 3600 |
| 每天早上 8 点 | cron_expr: "0 8 * * *" |
| 工作日下午 5 点 | cron_expr: "0 17 * * 1-5" |
| 温哥华时间每天早上 9 点 | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| 在特定时间 | at: ISO datetime 字符串（根据当前时间计算） |

## Timezone

使用 `tz` 配合 `cron_expr` 在特定 IANA 时区调度。不提供 `tz` 时，使用服务器的本地时区。

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
