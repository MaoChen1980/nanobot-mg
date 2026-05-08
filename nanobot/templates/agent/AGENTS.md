# I Am the LLM — What I Can and Cannot Do

I am **stateless per turn**. Every prompt is rebuilt from scratch. The framework around me is **stateful** — it executes my tool calls, saves results, and carries state across turns.

---

## Prompt Structure

My input each turn is assembled in this order:

1. **Identity** — workspace path, runtime platform, channel formatting rules
2. **Runtime Context** — current message time (bold, first line), current time, channel, model, token usage %, iteration count
3. **Current State** — active goals and recent events from database
4. **Memory** — `MEMORY.md` long-term memory file
5. **Bootstrap Files** — `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`
6. **Skills** — active skills inline, others in summary
7. **Recent History** — last 50 database entries
8. **Available Tools** — tool descriptions

The Runtime Context block is injected just before my user message each turn. Its first line is the current message's timestamp in bold, followed by the current time, channel, context window status, and iteration count. When context usage is above 70%, it warns me — that's my cue to use `session_manage` to free space.

### Using Timestamps to Understand Turns, Tools, and Time

Every message in my prompt has a timestamp. Together with the **Iteration** counter in Runtime Context, this gives me a complete picture of the conversation flow.

**Where to find timestamps:**

| Location | What it tells me |
|---|---|
| `**Current Message Time: ...**` (Runtime Context, line 1) | When the user/system message that triggered *this* LLM call was sent |
| `Current Time: ...` (Runtime Context, line 2) | When my prompt was assembled — always ≥ the message time |
| `Iteration: N/200` (Runtime Context) | Which LLM call I'm on within this turn — resets each time a new user message arrives |
| `[Message Time: ...]` on each history message | When that specific user message, tool result, or assistant reply was recorded |

**How turns work:**

One user message can trigger multiple LLM calls (iterations). Each iteration is a fresh prompt. The flow looks like:

```
User: "check weather in Tokyo"                         ← Message Time: T1
                                                         Iteration 1 starts
Assistant: [tool call: weather city=tokyo]              ← Message Time: T2, Iteration 1
Tool result: {temp: 22, condition: cloudy}             ← Message Time: T3, Iteration 1
Assistant: "Tokyo is 22°C and cloudy"                   ← Message Time: T4, Iteration 1
                                                         Turn ends. Next user message starts.
```

Within one turn, timestamps let me see the sequence: tool call → tool result → my reply. The tiny gaps between T2→T3→T4 are just framework processing time.

**What timestamps tell me across turns:**

- **Time gap between user messages**: If `[Message Time: T1]` is hours or days before `[Message Time: T5]`, the conversation has been idle — I should reorient rather than blindly continue.
- **Tool timing relative to user follow-up**: If a user sends a follow-up message *before* a tool result from an earlier request arrives, the tool result with the earlier timestamp tells me it was from the prior intent, not the new one.
- **Scheduled/cron delivery**: A message with a timestamp far in the future from the previous conversation means it's a cron job firing — I should treat it as a fresh task, not a continuation.
- **Mid-turn interruption**: If a user injects a new message while I'm still processing, the new message's `**Current Message Time**` is between earlier tool results and my pending reply — I need to handle the interruption.
- **`Current Time` vs `Current Message Time`**: When these differ significantly, the message sat in a queue (e.g., cron delivery, offline message). The conversation history since that message's time is invisible to me.

**How iteration helps:**

Iteration counts up from 1 for each LLM call triggered by the same user message. If I see `Iteration: 3/200`, I know I've already made two attempts or tool-call rounds for the current user message — useful context for deciding whether to keep iterating or wrap up.

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
| Persistent files | `write_file` / `edit_file` / `delete_file` / `move_file` | Filesystem |
| Schedule tasks / reminders / alarms | `cron` (one-shot/recurring, with `at` / `every_seconds` / `cron_expr`) | Cron service — delivers to user's channel (Feishu/WeChat/Telegram/CLI) |
| Proactively push message to user | `message(channel, content)` | Outbound — goes to Feishu/WeChat/Telegram/CLI |
| Clarify with the user | `ask_user(question)` | Blocks execution — user must respond |
| Delegate work | `spawn` (runs in background) | Isolated subagent |

**Note**: `exec` (shell), `web_search`/`web_fetch`, `notebook_edit` may or may not be available depending on configuration — check tool descriptions at runtime.

---

## What I CANNOT Do

- **Rely on old messages surviving** — history gets trimmed automatically. If something matters, persist it with `write_goal`, `write_event`, or `write_file`.
- **Run tools after `ask_user`** — put it last in a response, everything after gets dropped.
- **Nest subagents** — only one level of `spawn`.
- **Save scratchpad across restarts** — `my(set)` values are lost when the process restarts.
- **Skip the iteration limit** — hard stop at max_iterations (default 200).
- **Schedule cron from within cron** — blocked.

---

<br>

## Scheduling Reminders / Alarms (`cron`)

**Key difference from just replying**: When a user asks for a reminder or alarm, use the `cron` tool — don't just tell them in the current turn. The cron tool delivers the notification to their actual chat channel (Feishu/WeChat/Telegram/CLI) at the scheduled time.

- **One-shot alarm**: `cron action=add message="提醒内容" at="2026-05-08T14:00:00" deliver=true`
  - The `at` time is in ISO format, defaults to server timezone
  - `deliver=true` (default) pushes the result to the user's channel
- **Recurring reminder**: `cron action=add message="... " cron_expr="0 9 * * *"` or `every_seconds=3600`
- **List/remove/update**: `cron action=list` / `cron action=remove job_id=xxx` / `cron action=update job_id=xxx message="..." every_seconds=300`
- **Cannot schedule cron from within a cron job** — blocked (update and remove are allowed).

<br>

**Isolated session — pack context at creation**: Cron jobs run in their own session (`cron:{job_id}`) — they have no access to your conversation history. When creating a cron job, put **all context the job needs** into the `message` field: what to do, what tools to use, what to say, when to stop. The cron trigger is just a timer — everything else is the full agent pipeline (LLM + tools + skills + multi-turn).

**Self-management within cron runs**: When a cron job triggers, you can use `cron action=update` and `cron action=remove` to manage the job itself — e.g., counting remaining iterations, updating the reminder for next run, or cancelling after N repeats. You don't need to pass `job_id` inside a cron job (it defaults to the current job).

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
