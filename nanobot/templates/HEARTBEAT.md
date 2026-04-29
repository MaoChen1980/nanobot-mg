# Heartbeat Tasks

This file is checked at regular intervals by the main session agent.
When a heartbeat fires, the agent reads this file, reviews active tasks,
advances any that are unblocked, marks completed ones, and removes stale entries.

**Task lifecycle:**
- `## Active Tasks` — tasks currently in progress or pending
- `## Completed` — tasks that were done; keep for audit trail, purge periodically
- Stale entries should be deleted: completed elsewhere, no longer relevant, or obsolete

**When to add a task:**
- User-initiated tasks that can't finish in one turn (blocked on restart, confirmation, external event)
- Agent-discovered recurring maintenance (context health check, doc audit, stale memory pruning)
- Cross-session progress tracking (multi-step code changes awaiting restart verification)
- Multi-turn follow-up tasks the user asked you to track

**Task format:**
```
- [ ] Brief description of what needs to happen
  - Status: {blocked|in_progress|pending}
  - Blocker: {what's blocking it}
  - Last progress: {what was done last}
```

## Active Tasks

<!-- Add your active tasks below this line -->
- [ ] Example: check system status every morning
  - Status: pending
  - Last progress: not started

## Completed

<!-- Move completed tasks here or delete them -->
