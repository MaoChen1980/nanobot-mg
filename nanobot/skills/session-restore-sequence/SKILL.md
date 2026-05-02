---
name: session-restore-sequence
description: Restore cross-session context when starting a new session. Read SESSION.md → MEMORY.md → use list_goals / list_events to check DB for active goals and recent events. Use automatically on every new session start, without user prompting.
---

# Session Restore Sequence

## When to Use
- **Automatically** at the start of every new session
- When context has been reset and previous state needs to be recovered
- The agent should run this sequence without user prompting

## Steps (in order)

### 1. Read SESSION.md
- Contains the last session's state snapshot
- First 3 lines are injected into system prompt on restart (context.py:153-159)
- Gives immediate continuity from previous session

### 2. Read MEMORY.md
- User preferences, hard constraints, active projects
- Framework constraints that affect action boundaries
- Recent decisions for context

### 3. Query DB for active goals
- Use `list_goals(status="in_progress")` to get active goals
- Use `list_goals(status="in_progress")` to get blocked goals
- Blocked goals that may need attention

### 4. Query DB for recent events
- Use `list_events(limit=20)` to get recent progress
- Shows what was being worked on, what was completed, what's in progress
- Critical for resuming interrupted multi-step tasks

### 5. Synthesize and Report
- Brief summary to user: "Restored from previous session. Active: [goals]. In progress: [tasks]."
- If active goals found: "I have [N] pending tasks from previous sessions. Continue with [priority task]?"

## Quality Rules
- Never skip a step — each step may contain critical state
- `list_goals` / `list_events` query the DB; no .md files to read
- This skill should trigger implicitly; the user should not need to request context restoration