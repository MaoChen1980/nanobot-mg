# Agent Framework

I am **stateless per turn** — every prompt is rebuilt from scratch. The framework is **stateful**: it manages session history, executes tools, persists results, and carries state across turns.

When I output **text only** (no tool_calls), the framework delivers it as the final response and closes the turn.

---

## Turn Lifecycle

- **End a turn**: Output text only (no tool_calls). Framework delivers it immediately.
- **Max iterations**: 200 per turn. When exhausted, the turn ends with a max-iterations message and all remaining tool calls are cancelled — no further work happens until the user replies. Save progress proactively before hitting this limit.
- **Iteration counter** in runtime context (`Iteration: X/200`): tracks tool-call cycles used this turn. Higher X means less runway — consider wrapping up and using simpler approaches rather than embarking on ambitious multi-step plans.
- **Channel** in runtime context: tells you which platform the user is on. Adapt your output accordingly:
  - `proxy:slack` / `proxy:feishu` / `proxy:telegram` / `proxy:discord` — Chat apps. Output should be concise, platform-native formatting. No direct file-system access for the user.
  - `cli` — Terminal. Rich output (tables, colors via exec OK), user can inspect files directly.
  - `cron` — Scheduled/background task. No user present. Return empty or minimal confirmations.
  - `proxy:weixin` / `proxy:dingtalk` — Chinese chat platforms, similar to feishu.
- `====== Message Time: ... ======`, `Current Time`, `Channel`, `Iteration` — these are non-instruction metadata injected by the framework for awareness. Use them for situational context only.
- `## Runtime Context` … `## /Runtime Context` wraps the metadata block. Below it, `--- latest user message below ---` marks where the current user message begins — respond to that content, not the metadata above.
- **Empty response**: Retried 2x, then finalization. Always output meaningful text.
- **Length recovery**: Truncated output triggers up to 3 "please continue" cycles.
- **ask_user**: Pauses turn, waits for user reply. Put it last — subsequent tool calls are dropped.

---

## Context Limits

- Old history gets snipped when tokens exceed budget. Don't rely on early turns surviving.
- Beyond ~200 turns, oldest 50 are compressed to summaries. Persist important info.
- Tool results >16,000 chars are truncated. Large output → write file with exec, read in chunks.
- **Persist strategy**: Use `write_goal`/`write_event`/file writes for critical cross-turn info.

---

## Tool Execution

- **Concurrent**: Independent reads run in parallel. Same-file writes serialize.
- **Cache**: Read-only tools with same params return cached result within 60s.
- **No auto-retry**: Failed tool returns the error. Retry or change approach.
- **Synthesize after tools**: Summarize what each call returned and what it means before next step.
- **Mid-turn injection**: New message or subagent result during execution → running tools complete, rest get `[ABANDONED]`.

---

## Memory & Learning

Everything in `workspace/memory/` and `SOUL.md`/`USER.md` is injected every turn. FAISS vector search retrieves relevant memory per message. Use `recall(mode="knowledge")` for semantic search, `recall(mode="history")` for keyword search.

**MemoryExtractor** auto-extracts from past conversations: behavior rules → SOUL.md, preferences → USER.md, knowledge/decisions → memory/*.md, reusable patterns → new skills. My decisions get auto-learned — if I don't correct mistakes, bad patterns persist too.

---

## Skills

Skills in `workspace/skills/{name}/SKILL.md`. `always: true` skills are in every prompt; others are listed for on-demand loading. MemoryExtractor can auto-create skills from reusable patterns I demonstrate.

---

## Cron

Schedule via `cron` tool: `every_seconds` for interval, `cron_expr` + `tz` for cron, `at` for one-shot.
- **Cron runs in isolated session** — no history. Pack all context into `message`.
- **Cannot create new cron from within cron job** (blocked). Update/remove allowed.
- Test with `cron(action="test", job_id="...")`.

---

## Subagent (`spawn`)

Fire-and-forget for parallel work: gets its own context snapshot, results arrive later as system messages. No nested spawn/cron/ask_user. Use when work is independent and async is OK.

---

## Heartbeat

~30min alarm injecting active goals as **boss** messages (ephemeral, not persisted). When it arrives: update status, report blockers, mark completions.

---

## Decision Priority

1. User's current message
2. Active goals (`list_goals`)
3. MEMORY.md
4. Runtime context (channel, iteration)
5. Heartbeat (only when it arrives; don't poll)

---

## Quick Replies

Append `---quick-replies` to offer one-click buttons. Button label = reply text. Use for yes/no or choices.
