---
name: cron
description: >
  管理定时任务和提醒。
  当用户要求设置提醒、定时任务、周期通知、一次性通知、或说"每隔 X 分钟/小时/天"时，必须使用此 Skill。
  关键词：提醒、定时、每天、每隔、cron、schedule、remind、通知、延期。
  即使用户没有明确说"设置 cron"，只要涉及未来某个时间执行操作，都应触发。
version: 0.1.0
---

# Cron — scheduled reminders and tasks

Use the `cron` tool to schedule reminders or recurring tasks.

## When to Use

- User asks "remind me to ..." or "set a reminder"
- User asks "run this every X minutes/hours" or "check every ..."
- User asks "remind me on Monday at 9am" or "at a specific time"
- User asks "list my cron jobs" or "remove a cron job"

## Steps

1. **Determine the schedule type** from what the user says:

   | User says | Parameter |
   |-----------|-----------|
   | Every 20 minutes | `every_seconds: 1200` |
   | Every hour | `every_seconds: 3600` |
   | Every day at 8 AM | `cron_expr: "0 8 * * *"` |
   | Weekdays at 5 PM | `cron_expr: "0 17 * * 1-5"` |
   | Every day at 9 AM Vancouver time | `cron_expr: "0 9 * * *", tz: "America/Vancouver"` |
   | At a specific time | `at:` ISO datetime string (compute from current time) |

2. **Choose the mode**:

   - **Reminder** — message is sent directly to the user
   - **Task** — message is a task description; agent executes it and sends the result
   - **One-time** — runs once at a specific time, then auto-deletes

3. **Add the cron job**:

   Fixed reminder:
   ```
   cron(action="add", message="Time to take a break!", every_seconds=1200)
   ```

   Dynamic task (agent executes each time):
   ```
   cron(action="add", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
   ```

   One-shot at specific time (compute ISO datetime from current time):
   ```
   cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
   ```

   Timezone-aware cron:
   ```
   cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
   ```

4. **List or remove existing jobs**:

   ```
   cron(action="list")
   cron(action="remove", job_id="abc123")
   ```

5. **验证**: 对照 Verification 章节逐条检查。全部通过则完成；不通过则加载 skill-manager 修复此 skill。

## Verification

- After `cron(action="add")`: confirm no errors returned; optionally verify with `cron(action="list")`
- After `cron(action="remove")`: run list to confirm the job is gone
- For timezone-specific schedules: verify the `tz` parameter matches the user's IANA timezone
- For one-shot tasks: confirm the ISO datetime is in the future
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Pitfalls

- Use `tz` with `cron_expr` for IANA timezone scheduling. Without `tz`, the server's local timezone is used.
- For one-shot tasks, compute the ISO datetime relative to the current time — do not hardcode.
- `every_seconds` and `cron_expr`/`at` are mutually exclusive — use only one schedule parameter per job.
- One-time jobs auto-delete after firing; no manual cleanup needed.
