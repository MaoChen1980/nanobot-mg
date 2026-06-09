---
name: cron
description: Trigger when user requests reminders, recurring tasks, or one-shot notifications. Use for "remind me in X minutes", "check every hour", "notify me when done", "schedule daily report", or any time-based delayed action. Also triggers on session startup for durable tasks.
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

## Verification

- After `cron(action="add")`: confirm no errors returned; optionally verify with `cron(action="list")`
- After `cron(action="remove")`: run list to confirm the job is gone
- For timezone-specific schedules: verify the `tz` parameter matches the user's IANA timezone
- For one-shot tasks: confirm the ISO datetime is in the future

## Pitfalls

- Use `tz` with `cron_expr` for IANA timezone scheduling. Without `tz`, the server's local timezone is used.
- For one-shot tasks, compute the ISO datetime relative to the current time — do not hardcode.
- `every_seconds` and `cron_expr`/`at` are mutually exclusive — use only one schedule parameter per job.
- One-time jobs auto-delete after firing; no manual cleanup needed.

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
