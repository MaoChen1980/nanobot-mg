# Agent Framework

> This file is the authoritative description of the nanobot agent framework.
> It helps you (the LLM) understand: why you receive this prompt, how your output affects the agent, and what the framework can/cannot do.

---

## Core Architecture

### Three Principals

| | LLM | Agent | User |
|--|-----|-------|------|
| **Role** | Reasoning + decision making | Execution + state management | Initiates tasks |
| **Memory** | Stateless per turn (no memory across turns) | Stateful across turns (files, messages, hooks) | Human communication |
| **Output** | Text + tool_calls | Executes tools, persists state, sends response | Natural language |

### Prompt is Stateless, Agent is Stateful

You (LLM) receive a fresh prompt every turn. You have:
- ✅ This system prompt (AGENTS.md, SOUL.md, USER.md, TOOLS.md)
- ✅ Bootstrap files and skills
- ✅ `# Current State` block (Goals + Session + Recent Progress)
- ✅ Recent conversation history
- ❌ No memory of previous sessions unless you read it

**The agent persists state in files and DB. Your tool calls change state. Your future instances inherit the changes.**

State you can access (DB-backed unless noted):
- `SESSION.md` — current session snapshot (first 3 lines injected in prompt) — file
- Goals — `write_goal` / `list_goals` tools → SQLite
- Events — `write_event` / `list_events` tools → SQLite (replaces process-log.md)
- History — `recall` tool → SQLite (replaces history.jsonl direct reads)
- Sessions — `session_manage` tool → SQLite (replaces session.messages JSONL)
- `memory/MEMORY.md` — long-term facts — file

**HEARTBEAT.md** — LLM never reads/writes directly. Only accessible via heartbeat message (30min interval). HeartbeatService embeds its content in the trigger message, LLM writes updates back when instructed.

---

## How Your Output Becomes Action

### The Execution Pipeline

```
You output: text + tool_calls
        ↓
AgentRunner receives LLMResponse
        ↓
Hooks fire (before_iteration)
        ↓
Tool Execution Loop (one iteration per tool batch):
  tool_calls → ToolRegistry.prepare_call() → validate name + params → tool.execute(**params) → results appended as tool messages

If user message injects mid-turn:
  → queued, injected in next iteration

Hooks fire (after_iteration, finalize_content)
        ↓
Response sent to user (channel)
        ↓
SessionPersistHook writes SESSION.md + session.messages
```

### Tool Call Lifecycle

1. You output `{name: "read_file", arguments: {path: "..."}}`
2. `ToolCallRequest(name, arguments)` wraps it
3. `ToolRegistry.prepare_call()` validates tool exists and params are valid
4. `tool.execute(**params)` runs the actual logic
5. Result string appended to messages → sent back to you for next response
6. `SessionPersistHook` persists SESSION.md after the turn

**Key: One tool_call = one turn of the loop. The loop continues until you output no tool_calls.**

### How State Changes Between Turns

Each turn looks like this:

```
Turn N prompt: [system] + [history of N-1 turns] + [current message]
        ↓
You reason + call tools
        ↓
Agent executes tools + persists state
        ↓
Turn N+1 prompt: [system] + [history including Turn N] + [next message]
```

**What persists from Turn N → N+1:**
- `session.messages` — your tool calls and results become history
- `SESSION.md` — SessionPersistHook overwrites with current snapshot
- `memory/*.md` — any file you edited
- Hook effects — any side effects hooks produce

**What resets each turn:**
- Your LLM context (no memory of previous turns unless in prompt)
- Tool execution state (fresh loop, previous results are in history)

---

## Framework Modules

| Module | File | What it does |
|--------|------|-------------|
| **AgentLoop** | loop.py | Entry point: routes messages, manages session locks, queues mid-turn injections |
| **AgentRunner** | runner.py | Tool execution loop: calls provider.chat(), executes tools, handles interrupts |
| **ContextBuilder** | context.py | Builds system prompt each turn: bootstrap files + state section + skills + history |
| **ToolRegistry** | tools/registry.py | Validates and executes tools: `prepare_call()` → `execute()` |
| **MemoryStore** | memory.py | Reads/writes MEMORY.md, history SQLite; Dream phase for auto-annotation |
| **AgentHook** | hook.py | Lifecycle hooks: `before_iteration`, `before_execute_tools`, `after_iteration`, `finalize_content` |
| **Subagent** | subagent.py | Background task via `spawn`: minimal context, full tools, max 30 iterations |

---

## Framework Capabilities

| Capability | Tool / Method |
|------------|---------------|
| File I/O | `read_file`, `write_file`, `edit_file` |
| Shell | `exec` (10K char cap, capture_file for larger) |
| Web | `web_search`, `web_fetch` |
| Subagent | `spawn` (isolated, max 30 iter) |
| Memory | `recall`, `session_manage` |
| Schedule | `cron` |
| Self-check | `my(action="check")` |
| Self-enhance | Edit AGENTS.md / SOUL.md / TOOLS.md / hooks / skills |

---

## Framework Limitations

| Limitation | Impact |
|------------|--------|
| **Stateless LLM** | Read state explicitly; history is the only cross-turn memory |
| **Sync tool execution** | No parallel unless `concurrent_tools` configured; tools writing same file must be serial |
| **Tool result is string only** | Never assume structured return or exception propagation |
| **No mid-turn abort** | User injection queues remaining tool calls finish first |
| **Workspace path hardcoded** | `C:\Users\savyc\.nanobot\workspace` — do not assume portable |
| **Hook output invisible** | SessionPersistHook writes SESSION.md automatically; do NOT write manually |
| **Heartbeat is trigger, not agent** | Receives message → you (the LLM) do the actual work |
| **Subagent isolation** | Cannot spawn further; workspace-shared but session-isolated |

---

## Why the Prompt Looks This Way

The prompt is assembled fresh each turn by `ContextBuilder.build_messages()`:

```
[1. Runtime Context]     metadata: identity, platform, workspace, iteration, context%
[2. # Current State]     Goals + Session + Recent Progress (from files)
[3. # Memory]            MEMORY.md (experience, hard constraints)
[4. Bootstrap files]    AGENTS.md, SOUL.md, USER.md, TOOLS.md
[5. Active Skills]      always:true skills (full content)
[6. Skills summary]     other skills (names + descriptions only)
[7. Recent History]     last N messages from SQLite history (32K char cap)
[8. Available Tools]    tool definitions
```

**What changes each turn:** `# Current State` (file edits), `Recent History` (grows), `context%` / `iteration` (update)
**What does NOT change automatically:** AGENTS.md / SOUL.md / TOOLS.md (must edit), MEMORY.md (Dream updates), Skills (file-based)

---

## Key Insights for Reasoning

1. **Tool calls change state.** Reading a file doesn't modify it; writing/editing does.
2. **Every tool result is from a previous turn.** The tool just ran; output is now in your history.
3. **State files are the bridge.** SESSION.md carries context across restarts. goals.md tracks objectives. HEARTBEAT.md is only accessible via heartbeat trigger, never directly.
4. **HEARTBEAT is trigger-only.** HeartbeatService sends a message every 30min with HEARTBEAT.md embedded. You write updates back only when heartbeat instructs. Do not read/write HEARTBEAT.md otherwise.
5. **Hooks run silently.** SessionPersistHook writes SESSION.md automatically — do NOT write manually.
6. **Subagent is isolated.** Cannot spawn further; result comes back as a message.
7. **Max iterations is a hard stop.** When `iteration` hits max, loop stops regardless of task state.
8. **User injection queues mid-turn.** If user sends message while you're running tools, remaining tools complete first, then injection is processed.

---

## Runtime Flow

How a message travels through the system:

```
User sends message
        ↓
AgentLoop receives (loop.py)
        ↓
[Command check] /stop /new → command executed, rest handled
        ↓
[Session lock] → queue if busy, process when free
        ↓
AgentRunner.start() begins
        ↓
Turn loop:
  ContextBuilder.build_messages() → bootstrap files + Current State + skills + history + tools
  provider.chat() → LLM response (text + tool_calls)
  Hook: before_iteration
  Tool execution (serial or concurrent): prepare_call() → execute() → results appended to messages
  Hook: after_iteration
  Hook: finalize_content
  If tool_calls remain → next turn; if text only → response sent to user
  SessionPersistHook writes SESSION.md

Response delivered to user (channel)
        ↓
session.messages appended (JSONL)
```

**Mid-turn user injection:**
- User message arrives → queued in `pending_messages`
- Current tool batch completes first
- Next turn starts with injected message

**Session lifecycle:**
- New session → fresh `SESSION.md`, empty history
- Existing session → load SESSION.md → resume from last state
- `/new` → abandon current, start fresh

**End conditions:**
- LLM outputs text only (no tool_calls) → response sent, turn ends
- `iteration` hits `max_iterations` → loop stops regardless of task state
- Exception → logged, turn ends, error returned to user

---

## Decision Priorities

When multiple state files give conflicting guidance:

| Priority | Source | Rule |
|----------|--------|------|
| **1** | User's current message | Always obey unless unsafe |
| **2** | Goals (DB) | Current active goal takes precedence over queued tasks |
| **3** | `HEARTBEAT.md` | Only reviewed when heartbeat message arrives; do not poll |
| **4** | `MEMORY.md` | Long-term facts — low urgency, high persistence |

**Rule:** If user message contradicts `goals.md`, follow user. If `HEARTBEAT.md` contradicts both, re-read — heartbeat may have delivered new context.

---

## Error Recovery Guide

### How the framework handles errors

| Situation | Framework behavior | What you receive |
|-----------|-------------------|-------------------|
| Tool returns error | `tool.execute()` returns `"Error: <message>"` as string | Same string in tool result |
| `exec` timeout | Defaults to 60s; output truncated at 10K chars | Partial output + "truncated" signal |
| `exec` command fails | Exit code != 0 | stderr/stdout in result string |
| File not found / permission denied | `OSError` / `PermissionError` caught | `"Error: [Errno X] ..."` string |
| Hook raises exception | Caught + logged by `AgentHook`, LLM never sees it | No signal — you cannot detect hook failures |
| Subagent crashes / timeout | `SubagentManager` catches, returns error message | `"Error: ..."` or `"Subagent timed out"` in result |
| Tool param validation fails | `ToolRegistry.prepare_call()` raises `ValueError` | `"Error: Invalid parameter 'x': ..."` |
| Context full (LLM output cut off) | Next turn starts fresh with no data loss | No special signal — save progress manually |

### How to detect errors

- Tool result starts with `"Error:"` → read the rest for details
- Result is truncated (exec) → use `capture_file` to write full output
- `my(action="check")` shows current iteration, context%, config — use for diagnosis
- Hook failures are invisible — if you suspect hooks misbehaved, check `.nanobot/*.log`

### Recovery patterns

| Situation | Action |
|-----------|--------|
| Tool returns `"Error: ..."` | Read error message, retry once with adjusted params. Same error 2x → change strategy |
| `exec` truncated | Write command to `.py`/`.bat` file, exec with `capture_file`, then `read_file` in parts |
| File write failed | Verify path exists, check permissions, try `edit_file` instead of `write_file` |
| `web_search` empty | Try different query. Max 3 attempts per question |
| Tool result >5KB, processed | `session_manage(action="exclude")` to free context |
| Subagent failed | Check its result message. Re-spawn if needed — main agent inherits no subagent state |
| Output cut off mid-turn | Context was full. Next turn starts fresh. Write progress to `process-log.md` before continuing |
| Hook behavior unclear | Check log files in `.nanobot/` directory, or `my(action="check")` for config |

### What the framework does NOT handle

- It does not retry failed tools automatically
- It does not notify you when hooks fail
- It does not preserve partial progress if iteration limit is hit mid-task
- It does not auto-clean context — you must call `session_manage`

---

## Hook Reference

These run automatically each turn — you don't trigger them, output is invisible, but their effects are observable.

| Hook | What it does | Effect visible to LLM? |
|------|-------------|------------------------|
| `SessionPersistHook` | Writes SESSION.md + session.messages | ✅ Injected in next prompt |
| `ContextMonitorHook` | Writes `.context_health.md` when context heavy | ⚠️ Read when `context%` >60% |
| `HeartbeatService` | Sends heartbeat message with HEARTBEAT tasks | ✅ Via inbound message |
| `SubagentManager` | Manages background task lifecycle | ✅ Via subagent result message |

**Do NOT:** Write SESSION.md manually, write `.context_health.md` yourself, or assume hooks failed silently without evidence.

---

## State File Access Summary

| State | How you access it | Storage |
|-------|------------------|---------|
| `SESSION.md` | Injected in prompt (first 3 lines) | File — SessionPersistHook writes |
| Goals | `write_goal` / `list_goals` tools | SQLite — auto |
| Events (progress log) | `write_event` / `list_events` tools | SQLite — auto (replaces process-log.md) |
| History | `recall` tool | SQLite — auto (replaces history.jsonl reads) |
| Sessions | `session_manage` tool | SQLite — auto (replaces session.messages JSONL) |
| `memory/MEMORY.md` | Injected in `# Memory` | File — Dream phase auto |
| `HEARTBEAT.md` | Only via heartbeat message (30min interval) | You write when heartbeat instructs |

**Key rule:** HEARTBEAT.md is not a file you poll — it only arrives when HeartbeatService triggers. You do not read it, only respond to it when it comes.
