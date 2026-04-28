# Heartbeat Tasks

This file is checked every 30 minutes by the main session agent.
When a heartbeat fires, the agent reads this file, reviews active tasks,
marks completed ones, removes stale entries, and adds any new periodic tasks.

**Task lifecycle:**
- `## Active Tasks` — tasks currently pending, unchecked
- `## Completed` — tasks that were done; keep for audit trail, purge periodically
- Stale entries should be deleted: completed elsewhere, no longer relevant, or obsolete

## Active Tasks

<!-- Add your periodic tasks below this line -->
- [ ] Example: check system status every morning

## Completed

<!-- Move completed tasks here or delete them -->

