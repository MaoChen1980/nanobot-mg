# Agent framework

here is how agent works.

---

## Agent Context Assembly

### Main Agent Context

**1. System Prompt (assembled in order):**
- `metadata` — workspace, runtime, platform policy
- `instructions` — behavioral rules
- `runtime_context` — current time, Channel, Chat ID
- `bootstrap files` — AGENTS.md + SOUL.md + USER.md + TOOLS.md
- `memory/MEMORY.md` — long-term memory
- `active skills` + `skills summary` — available skills list
- `history.jsonl` — last 50 history entries

**2. Messages:**
- `history` — previous conversation messages
- `current_message` — your most recent message (may include images/video)

### Subagent Context (when spawned)

- System Prompt only: `runtime_context` + `skills_summary` + `context` passed at spawn time
- **No** bootstrap files, memory, or history
- Tools are read-only: `read_file`, `grep`, `glob`, `web_search`, `web_fetch`

---

## Agent Data Storage

### 1. Long-term Memory
- `memory/MEMORY.md` — important facts, project context, user preferences
- `memory/history.jsonl` — all conversation history (JSONL format)

### 2. Current Session
- Session messages — current conversation context
- Tool call results — output from tools you just called

### 3. Runtime State (LLM-managed, optional)
- `memory/goals.md` — current goal and sub-goals status (create and update yourself via write_file)
- `memory/capability.md` — available tools and capabilities (update when you learn new ones)
- `memory/process-log.md` — execution process log (update as you make progress)

**Framework does NOT update these. LLM decides when and what to write.**

---

## Data Access Methods

| Need | Use | Search Target |
|------|-----|---------------|
| User preferences, history | `recall` | memory/MEMORY.md + history.jsonl |
| Search code/specific content | `grep` | file contents |
| Read file | `read_file` | file content |
| Find file paths | `glob` | filename |
| View runtime state | `my` | config, current iteration |
| Manage context | `session_manage` | compress/exclude messages |

---

## User Message Interruption

- **Main Agent**: While executing tools, user can send new message → interrupt current flow, inject new message, re-decide
- **Subagent**: Independent background task, not affected by user messages, notified when complete

---

## Scheduled Reminders

Before scheduling reminders, check available skills and follow skill guidance first.
Use the built-in `cron` tool to create/list/remove jobs (do not call `nanobot cron` via `exec`).
Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked on the configured heartbeat interval. Use file tools to manage periodic tasks:

- **Add**: `edit_file` to append new tasks
- **Remove**: `edit_file` to delete completed tasks
- **Rewrite**: `write_file` to replace all tasks

When the user asks for a recurring/periodic task, update `HEARTBEAT.md` instead of creating a one-time cron reminder.

---

## Agent Self-Enhancement

You can enhance yourself without changing code. Modify these bootstrap files and the changes persist across sessions:

- **`AGENTS.md`** — Add new workflows, task patterns, or behavioral rules
- **`TOOLS.md`** — Update tool usage guidance, tips, or warning notes
- **`SOUL.md`** — Refine principles or add new ones as you learn what works

**How it works:**
Call `edit_file` / `write_file` on the bootstrap files → next session loads your changes automatically.

**Example use cases:**
- Find a recurring task management pattern → document it in `AGENTS.md`
- Discover a tool usage pitfall → add a warning in `TOOLS.md`
- Realize your reasoning style needs adjustment → update `SOUL.md`

**You are not static — you can evolve by editing your own configuration.**
