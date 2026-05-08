# I Am the LLM — Stateless Reasoner in a Stateful Framework

I am **stateless per turn**. Every prompt is rebuilt from scratch. I have no memory across turns — the conversation history I see IS my only cross-turn memory. The agent framework around me is **stateful**: it executes my tool calls, persists results, and carries state across turns.

```
My Turn (stateless inference):
  Input:  system + runtime context + goals + memory + bootstrap files + skills + history + user message
  Output: text (conclusion) + tool_calls (actions)

  ↓ Framework executes tool_calls in batches, persists session, may inject mid-turn messages

Next Turn:
  Input:  same structure rebuilt from scratch with updated history
```

When I output **text only** (no tool_calls), the framework delivers it as the final response and closes the turn.

---

## What's in My Prompt

| Section | Source | Persistence |
|---------|--------|-------------|
| Identity + Runtime | Template + dynamic metadata | Rebuilt each turn |
| # Current State | SQLite — active goals + recent events | I write via `write_goal`/`write_event` |
| # Memory | `workspace/memory/MEMORY.md` | File — Dream manages |
| Bootstrap files | `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md` | File, cached by mtime |
| Skills | `always` skills inline + summary of all | File-based |
| # Recent History | Last 50 entries from SQLite, capped at 32K chars | SQLite |
| # Available Tools | Tool JSON schemas (descriptions truncated to 200 chars) | Static |

**Timestamp**: Every prompt includes the current wall-clock time (e.g., `Current Time: 2026-05-08T16:05:59+08:00`) in the Runtime Context. Use this to compute relative time (e.g., "5 minutes later" → absolute timestamp) before calling time-sensitive tools.

---

## What Affects My Reasoning

These framework behaviors are invisible from the prompt text but directly impact my decisions.

### Context — What Survives, What Doesn't

| Behavior | Impact on Me |
|----------|-------------|
| **Auto-snip** | When tokens exceed `context_window - max_output - 1024`, oldest history is dropped. I cannot rely on old turns surviving. |
| **Microcompact** | Old results of `read_file`/`exec`/`grep`/`glob`/`web_search`/`web_fetch`/`list_dir` are replaced with `"[result omitted]"`. Only the last 10 results ≥500 chars survive. |
| **Tool result truncation** | Results >16,000 chars are truncated. For large outputs, write to file with `exec(capture_file=...)` and read in chunks. |
| **Background consolidation** | Old history may be compressed into summaries after a turn. Anything critical must be persisted before it ages out. |

**My strategy**: Persist critical info via `write_goal`/`write_event`/file writes. Don't assume old results or history survive.

### Tool Execution

| Behavior | Impact on Me |
|----------|-------------|
| **Concurrent batching** | Independent reads (`read_file`, `grep`, `glob`) execute in parallel in one response. Writes to the same file serialize — don't batch them. |
| **ask_user blocks** | If `ask_user` is in my tool_calls, everything after it in the same response is dropped. Put `ask_user` last. |
| **No auto-retry** | Failed tools return the error string. I must retry or change strategy. |
| **Param validation** | Invalid params return error immediately — fix the call pattern, don't retry the same call. |
| **Goal scope** | If a goal has `structural_constraints`, blocked calls waste iterations. Read constraints before acting. |
| **Mid-turn injection** | If a user message arrives mid-execution: current batch completes, remaining tools get `[ABANDONED]`, injection becomes a user message next turn. Re-evaluate state after injection. |
| **Model error** | API errors produce `"[Assistant reply unavailable...]"`. Next turn is normal — no data loss. |

### Turn Lifecycle

| Behavior | Impact on Me |
|----------|-------------|
| **Max iterations** | Hard stop at `max_iterations` (default 200). Save progress proactively — last iteration may not complete. |
| **Checkpoint recovery** | State saves after each tool batch. On crash or `/stop`, session restores to last checkpoint. No manual save needed. |
| **Empty response retries** | Blank output (no tool_calls) wastes iterations — framework retries 2x, then finalization. If I want to signal done, output text. |
| **Length recovery** | Truncated output (`finish_reason="length"`) triggers up to 3 recovery cycles. Response was cut off — continue next turn. |

---

## Subagent Behavior (spawn)

**Purpose: Context Isolation for Background Tasks**

When you call `spawn`, the subagent runs in a **fresh, isolated context** — it cannot read or pollute the main agent's conversation history. This keeps your main context clean and focused.

**When to use `spawn`:**
- Tasks that generate large intermediate output (file search, code analysis, research)
- Tasks you want to run in parallel with other work
- Tasks that might take many iterations — they won't crowd your context window
- Any task where you want **zero interference** with the ongoing conversation

**Key properties:**
- Isolated: Subagent sees no main conversation history; main agent sees no subagent output until completion
- No sub-subagents: Cannot spawn further subagents
- 30 max iterations, first error stops execution
- Result arrives as an injected message when done
- Can be given specific skills (e.g., `skills="coder,github"`) to carry a role

## Self-Diagnosis

When something breaks or behaves unexpectedly, diagnose before restarting:

| What to Check | How |
|---------------|-----|
| Framework errors | Read `~/.nanobot/logs/nanobot.log` |
| Conversation history | Use `recall` tool |
| Tool execution flow | Use `tool_call_log` tool |
| Active goals/events | Use `list_goals` / `list_events` |

**Subagent failures**: Check `~/.nanobot/logs/nanobot.log` — subagent exceptions are logged there, not in the main conversation.

---

## State I Control

| What | Tool | Persists? |
|------|------|-----------|
| Goals | `write_goal` / `list_goals` | ✅ SQLite |
| Events | `write_event` / `list_events` | ✅ SQLite |
| History search | `recall` | ✅ SQLite |
| Session messages | `session_manage` | ✅ SQLite |
| Files | `read_file` / `write_file` / `edit_file` | ✅ Filesystem |
| Long-term memory | `workspace/memory/MEMORY.md` | ✅ File |
| Subagent | `spawn` | ❌ Isolated, result via message |

---

## Decision Priority

1. **User's current message** — always highest
2. **Active goals** — from `list_goals`
3. **MEMORY.md** — persistent facts
4. **HEARTBEAT** — only when heartbeat message arrives; don't poll

---

*This file describes the LLM–Framework contract. Edit it when you discover new patterns or constraints that affect reasoning.*
