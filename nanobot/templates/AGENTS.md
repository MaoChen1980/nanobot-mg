# Agent framework

here is how agent works.

---

## How an Agent Works

### Message Flow

```
User sends message
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. INBOUND                                                   │
│    Channel (Feishu/Slack/CLI/etc.) sends InboundMessage     │
│    to AgentLoop via message bus                              │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. DISPATCH (per-session serial)                           │
│    - Priority commands (/stop, /new) handled immediately    │
│    - Session lock ensures one task per session at a time    │
│    - If session busy: message queued for mid-turn injection│
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. CONTEXT ASSEMBLY                                         │
│    ContextBuilder.build_messages() produces:                 │
│    [system prompt] + [history] + [current message]          │
│                                                             │
│    System prompt includes:                                   │
│    - workspace metadata, runtime, platform policy           │
│    - bootstrap files (AGENTS.md, SOUL.md, USER.md,          │
│      TOOLS.md)                                              │
│    - memory/MEMORY.md (long-term facts, read-only)           │
│    - active skills + skills summary                           │
│    - recent history from session.messages                    │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. LLM CALL → Runner loop                                  │
│    AgentRunner.run() calls provider.chat()                  │
│    Provider returns LLMResponse                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. TOOL EXECUTION LOOP (iterations)                       │
│    For each LLM response:                                   │
│    a. If no tool_calls → return final content              │
│    b. If has tool_calls → execute tools in parallel        │
│    c. Collect results, append as tool messages             │
│    d. Send back to LLM for next response                   │
│    e. Max iterations reached → stop                        │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. SUBAGENT                                               │
│    LLM calls spawn tool → SubagentManager creates         │
│    independent background task with:                       │
│    - Minimal context (runtime_context + skills_summary)     │
│    - Read-only tools (read_file, grep, glob, web_* )      │
│    - Result announced back when complete                  │
│    User messages do NOT interrupt subagents               │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. RESPONSE                                               │
│    Final content sent via channel's OutboundMessage        │
│    AskUser options extracted if stop_reason == "ask_user"  │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. SESSION PERSISTENCE                                    │
│    - Messages saved to session.messages                    │
│    - Session saved to sessions/*.jsonl                     │
│    - File cap enforced (old messages archived)            │
└─────────────────────────────────────────────────────────────┘
```

---

## Agent Context Assembly

### Main Agent Context

**System Prompt (assembled in order):**
1. `metadata` — workspace, runtime, platform policy
2. `instructions` — behavioral rules
3. `runtime_context` — current time, Channel, Chat ID
4. `bootstrap files` — AGENTS.md + SOUL.md + USER.md + TOOLS.md
5. `memory/MEMORY.md` — long-term memory (read-only, managed by Dream)
6. `active skills` + `skills summary` — available skills list
7. `Recent history` — last 50 entries from session.messages

**Messages:**
- `history` — previous conversation messages (from session.messages)
- `current_message` — user's most recent message (may include images/video)

### Subagent Context (when spawned)

- Minimal system prompt: `runtime_context` + `skills_summary` + `context` passed at spawn time
- **No** bootstrap files, memory, or history
- Tools are read-only: `read_file`, `grep`, `glob`, `web_search`, `web_fetch`

---

## Agent Data Storage

### 1. Long-term Memory (Dream-managed)
- `memory/MEMORY.md` — important facts, project context, user preferences (read-only for LLM)
- `memory/history.jsonl` — all conversation history (append-only JSONL)

### 2. Current Session (runtime)
- `session.messages` — current conversation context, persisted to JSONL
- Tool call results stored within session messages

### 3. Runtime State (LLM-managed, optional)
- `memory/goals.md` — current goal and sub-goals status
- `memory/capability.md` — available tools and capabilities
- `memory/process-log.md` — execution process log

**Framework does NOT auto-load runtime state into context. LLM reads via read_file when needed.**

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

## Skills

Skills are loaded from `nanobot/skills/` and `workspace/skills/`.

- `always: true` skills are always included in system prompt
- Other skills: listed in skills summary, LLM decides when to use
- Skills content injected into `skills summary` section of system prompt
- Framework does not invoke skills automatically — LLM chooses

---

## User Message Interruption

- **Main Agent**: While executing tools, user sends new message → queued for mid-turn injection, processed after current turn
- **Subagent**: Independent background task, not affected by user messages, result announced when complete

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
