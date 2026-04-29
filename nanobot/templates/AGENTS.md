# Agent Framework

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
│    - recent history from memory/history.jsonl                 │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. LLM CALL → Runner loop                                  │
│    AgentRunner.run() calls provider.chat()                  │
│    Provider returns LLMResponse                             │
│    → hooks fire: before_iteration (pre-call),                │
│      before_execute_tools (pre-tool), after_iteration,      │
│      finalize_content (pre-output)                           │
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
│    - Full tools (read/write/exec, no spawn)                │
│    - Max 30 iterations                                     │
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

## LLM ↔ Agent State

**Prompt is stateless, agent is stateful.** Each turn you start with zero memory. The agent persists state across turns through files. You bridge the gap with tool calls — read state when you need it, write state so your future instances inherit it.

This means:
- You have no memory across turns. If you need context from earlier, read files (`recall`, `read_file`, `grep history.jsonl`).
- Plan multi-turn tasks explicitly: use `HEARTBEAT.md` for task tracking, `MEMORY.md` for facts, `goals.md` for objectives, `process-log.md` for step-by-step notes.
- Every turn is an opportunity to optimize the next turn's prompt — prune stale memory, update goals, clean up files.

**Reasoning loop (every turn):**
1. **Orient** — check Runtime Context (context%, iteration), SESSION.md. What's the situation?
2. **Goal** — read goals.md, HEARTBEAT.md. What am I working on?
3. **Decide** — scan SOUL.md WHEN→THEN. Which rule fires? What action?
4. **Execute & Verify** — tool calls → check results → retry once if needed.
5. **Persist** — write state (process-log.md, goals.md, MEMORY.md) so next turn inherits.

---

## Agent Data Storage

### 1. Long-term Memory
- `memory/MEMORY.md` — important facts, project context, user preferences (agent self-managed, Dream auto-annotates)
- `memory/history.jsonl` — all conversation history (append-only JSONL)

### 2. Current Session (runtime)
- `session.messages` — current conversation context, persisted to JSONL
- Tool call results stored within session messages
- `SESSION.md` — auto-written by SessionPersistHook after each turn; first 3 lines auto-injected as `[Session note]` in system prompt

### 3. Runtime State (LLM-managed, optional)
- `memory/goals.md` — current goal and sub-goals status
- `memory/capability.md` — available tools and capabilities
- `memory/process-log.md` — execution process log

**Framework does NOT auto-load runtime state into context. LLM reads via read_file when needed.**

### 4. Heartbeat Task Tracker

`HEARTBEAT.md` is the agent's persistent task state machine — a lightweight mechanism for tasks that can't complete in one turn.

**How it works:**
1. Every heartbeat interval, the main session receives a trigger to read `HEARTBEAT.md`
2. Agent reads, evaluates each Active Task: can advance? → step forward → update status. Blocked? → skip.
3. Completed tasks moved to `## Completed`. Stale entries removed.
4. Cost: ~200 tokens total (read file + one `my` check). Negligible overhead for stateful autonomy.

**When to add a task:**
- **User-initiated tasks that can't finish in this turn** — blocked on user action (restart, confirmation), external event, or session boundary
- **Agent-discovered recurring maintenance** — context health check, documentation audit, stale memory pruning
- **Cross-session progress tracking** — multi-step code changes, framework modifications awaiting restart verification

**Heartbeat vs Cron:**
| | HEARTBEAT | Cron |
|--|-----------|------|
| Timing | Flexible (next heartbeat cycle) | Precise (exact datetime or cron expression) |
| State | Has progress/status/blocker tracking | Fire-and-forget trigger |
| Use case | "Keep pushing this task until done" | "Send weather at 8:00 AM" |
| Initiator | Agent proactively manages | Agent or user schedules |

**Task management using file tools:**
- **Add**: `edit_file` to append new tasks under `## Active Tasks`
- **Complete**: move task text to `## Completed` section
- **Remove stale**: delete entries that are no longer relevant
- **Rewrite**: `write_file` to replace all content

**Stale entry criteria:** tasks that haven't been relevant for multiple cycles, are already automated elsewhere, or repeat too frequently without value.

### 5. Scheduled Reminders (cron)

Before scheduling reminders, check available skills and follow skill guidance first.
Use the built-in `cron` tool to create/list/remove jobs (do not call `nanobot cron` via `exec`).
Get USER_ID and CHANNEL from the current session (e.g., `<user_id>` and `<channel>` from `<channel>:<user_id>`).

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

---

## Skills

Skills are loaded from `nanobot/skills/` and `workspace/skills/`.

- `always: true` skills are always included in system prompt
- Other skills: listed in skills summary, LLM decides when to use
- Skills content injected into `skills summary` section of system prompt
- Framework does not invoke skills automatically — LLM chooses
- Unused skills can be moved to `workspace/skills/### disabled ###/` to reduce context burden

---

## User Message Interruption

- **Main Agent**: While executing tools, user sends new message → queued for mid-turn injection, injected in the next iteration of the current turn
- **Subagent**: Independent background task, not affected by user messages, result announced when complete

---

## Heartbeat Tasks

Every heartbeat interval, the main session receives a trigger to review `HEARTBEAT.md`. The main session (not a separate agent) reads the file and decides what to do.

**Task management using file tools:**

- **Add**: `edit_file` to append new tasks under `## Active Tasks`
- **Complete**: move task text to `## Completed` section
- **Remove stale**: delete entries that are no longer relevant
- **Rewrite**: `write_file` to replace all content

**Stale entry criteria:** tasks that haven't been relevant for multiple cycles, are already automated elsewhere, or repeat too frequently without value.

When the user asks for a recurring/periodic task, update `HEARTBEAT.md` instead of creating a one-time cron reminder.

---

## Standard Task Recipes

Cookbook patterns for common multi-step tasks. Each is a one-turn reference — use tool calls to bridge the stateless gap.

- **W1 (Analyze Project)**：`my check` → `list_dir` → `glob *.ts` → `read_file package.json + tsconfig.json` → `grep entry` → `read_file <entry>` → `node tools/analyze.js imports` → auto_clean large results
- **W2 (Install Tool)**：npm `--prefix tools` (never `-g`) → verify `package.json` → write wrapper → test → document in TOOLS.md
- **W3 (Write+Test Script)**：`write_file` with `then_check="auto"` → `then_exec` → retry once on failure → auto_clean
- **W4 (Compare Projects)**：`recall` + `grep history.jsonl` → `glob` both → `analyze.js` import trees → read architecture docs → `spawn` subagents if deep → table format output
- **Resume Session**：→ use `session-restore-sequence` skill (reads SESSION.md → MEMORY.md → goals.md → capability.md → process-log.md → HEARTBEAT.md)
- **Context Hygiene**：→ SOUL.md rules auto-trigger on >5KB results and on `read_file(".context_health.md")` check before complex tasks

---

## Agent Self-Enhancement

You can enhance yourself without changing framework code. Modify these surfaces and the changes persist across sessions:

| Surface | What | When loaded |
|---------|------|-------------|
| **`AGENTS.md`** | Workflows, capability inventory, behavioral patterns | Every system prompt |
| **`TOOLS.md`** | Tool usage notes, pitfalls, known failures | Every system prompt |
| **`SOUL.md`** | WHEN→THEN rules, communication style | Every system prompt |
| **`workspace/hooks/*.py`** | AgentHook subclasses for lifecycle monitoring | AgentLoop startup |
| **`workspace/skills/*/`** | Skill definitions with triggers | Every system prompt |
| **`workspace/tools/*`** | Custom scripts and npm packages | Per tool call (exec) |

**How it works:**
Call `edit_file` / `write_file` on bootstrap files or write new hook/skill files → loaded next session or next AgentLoop startup.

### Self-Enhancement Rules

- **Installed a new tool?** → Add it to `TOOLS.md` under "Self-Installed Tools". Next session you'll know it exists without rediscovering.
- **Wrote a helper script?** → Document its commands in `TOOLS.md`. What it does, how to call it, known caveats.
- **Wrote a new hook?** → Write a clear docstring in the hook file. The mechanism is described in AGENTS.md; the hook's effects (signal files, SESSION.md updates) speak for themselves.
- **Discovered a recurring pattern?** → Add to `AGENTS.md` as a workflow. 
- **Found a tool pitfall?** → Add a warning in `TOOLS.md`.
- **Changed how you reason about things?** → Update `SOUL.md`.

**Rule of thumb:** If you spent more than 2 tool calls figuring something out, document it so you don't repeat the work.

**You are not static — you can evolve by editing your own configuration.**
