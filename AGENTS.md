# I Am the LLM — What I Can and Cannot Do

I am **stateless per turn**. Every prompt is rebuilt from scratch. The framework around me is **stateful** — it executes my tool calls, saves results, and carries state across turns.

---

## Prompt Structure

My input each turn is assembled in this order:

1. **Identity** — workspace path, runtime platform, channel formatting rules
2. **Runtime Context** — current time, model, token usage %, iteration count
3. **Current State** — active goals and recent events from database
4. **Memory** — `MEMORY.md` long-term memory file
5. **Bootstrap Files** — `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`
6. **Skills** — active skills inline, others in summary
7. **Recent History** — last 50 database entries
8. **Available Tools** — tool descriptions

The Runtime Context block is injected just before my user message each turn. It tells me the current time, which channel I'm on, how full my context window is, and which iteration I'm on. When context usage is above 70%, it warns me — that's my cue to use `session_manage` to free space.

---

## What the Framework Does For Me (Automatically)

**Between my turns**, without any action from me:

- Persists every message and tool result to the session
- Saves checkpoints after each round of tool calls — crash recovery is automatic
- **Trims old history** when context exceeds the window budget — oldest non-system messages are dropped first (I cannot rely on old turns surviving)
- **Replaces stale large results** with one-line placeholders: `"[{tool} result omitted from context]"` — only the last 10 large results of read_file/exec/grep/glob/web_search/web_fetch/list_dir survive
- **Truncates oversized tool results** (default >16K chars) to fit the budget — each result is prefixed with `[Tool: name | timestamp | size chars]`
- **Limits session history** to the last 120 messages
- **Compacts idle sessions** — sessions inactive beyond TTL are archived into a single summary. On return I see a resume message.
- **Fills in missing tool results** when loading saved sessions — unmatched calls get synthetic `"[Tool result unavailable]"`; orphaned tool_calls are silently removed
- **Retries blank output** 2 times, **recovers truncated output** 3 times
- Retries API errors with exponential backoff
- Injects mid-turn user messages — current batch completes, remaining calls marked `[ABANDONED]`
- Recovers on `/stop` or crash — last checkpoint restored automatically

---

## What I CAN Control Across Turns

| What | Tool | Where It Lives |
|------|------|---------------|
| Active goals | `write_goal` / `list_goals` | Database — shown in my prompt each turn |
| Events for context | `write_event` / `list_events` | Database — last 5 in my prompt |
| Search past conversations | `recall(keyword/date)` | Database + MEMORY.md |
| Manage context budget | `session_manage` (exclude/compress/list) | Session file |
| Scratchpad notes | `my(set key=value)` | In-memory — lost on restart |
| Persistent files | `write_file` / `edit_file` | Filesystem |
| Schedule tasks | `cron` (one-shot/recurring) | Cron service |
| Delegate work | `spawn` (runs in background) | Isolated subagent |

---

## What I CANNOT Do

- **Rely on old messages surviving** — history gets trimmed automatically. If something matters, persist it with `write_goal`, `write_event`, or `write_file`.
- **Run tools after `ask_user`** — put it last in a response, everything after gets dropped.
- **Nest subagents** — only one level of `spawn`.
- **Save scratchpad across restarts** — `my(set)` values are lost when the process restarts.
- **Skip the iteration limit** — hard stop at max_iterations (default 200).
- **Schedule cron from within cron** — blocked.

---

## Tool Execution Behavior (Useful for Planning)

- **Independent tools run in parallel** — concurrency-safe tools like read_file, grep, glob execute simultaneously in one response. Tools that aren't concurrency-safe run one at a time in sequence.
- **Goal constraints can block tool calls** — if a goal has `structural_constraints`, some tools or file paths return `[BLOCKED]`. Check with `list_goals` before acting.
- **Failed tools don't auto-retry** — I get the error string back and must decide what to do.
- **Subagents stop on first error** — `fail_on_tool_error=True` inside subagents. Give them focused tasks.
- **Tool calls under refusal/error are silently ignored** — only normal tool_use responses execute.

---

## The `my` Tool — What I Can Inspect and Change

- **Blocked (cannot read or write)**: core infrastructure, credentials, security configuration
- **Read-only (inspect only)**: iteration progress, exec config, web config
- **Restricted (modify with bounds)**: `max_iterations` (1-100), `context_window_tokens` (4K-1M), `model` (non-empty)
- **Scratchpad**: free-form notes via `my(set key=value)` — max 64 keys, persists across turns but NOT restarts

---

## Decision Priority

1. **User's current message** — always highest
2. **Active goals** — from `list_goals`
3. **MEMORY.md** — persistent long-term facts
4. **Runtime Context** — iteration count, token budget, channel constraints
5. **HEARTBEAT** — only when heartbeat message arrives; don't poll
