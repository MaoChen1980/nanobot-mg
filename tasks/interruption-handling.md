# Mid-Turn Interruption Handling

## Background

When a user sends a new message while tool calls are executing, the runner
injects the new user message mid-turn. The current implementation uses
`[BYPASSED]`/`[PENDING]`/`[CANCELLED]` markers as fake tool results, which
(1) violates the `assistant → tool → user` role alternation rule that all
major LLM APIs enforce, and (2) leaves unexecuted `tool_calls` in the
original assistant message.

## Rule

```
assistant → tool → [tool → ...] → assistant → user → assistant → ...
```

`tool → user` is illegal. After any interruption, the tool chain must be
properly closed with an assistant message before appending the new user
message.

## Principle: Neutral Closing Assistant

The program cannot understand the user's intent. The closing assistant
restates facts and adds a neutral directive:

1. **Facts**: Which tool calls completed / were pending
2. **Directive**: "用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。"

This tells the LLM to prioritize the user's new message without committing
to resuming the original task. The "可以并行处理" gives the LLM flexibility
to handle both original and new tasks when appropriate, without forcing it.

The LLM sees the facts + directive + user message and naturally interprets
intent — redirect, supplement, or temporary interruption.

## Technical Scenarios (by execution state)

### Scenario 1 — `content=""` (tool-call-only assistant)

The original assistant declares tool_calls but some or all were not executed
when the user sent a new message.

### Scenario 2 — `content="回复内容"` (has real text)

Same structural rules as Scenario 1. The synthetic closing assistant should
preserve the original text content when applicable.

### Scenario 3 — Tool error / user cancellation, no injection

```
assistant("", [tc1, tc2, tc3]) → tool(tc1, ok) → tool(tc2, Error) → tool(tc3, [CANCELLED])
```

**3a — Single tool error**: tool returns a real error. LLM sees it and can
retry or switch approach. **No intervention needed.**

**3b — Partial + cancellation**: User cancelled (`/stop`) or cascade failure.
Tool chain ended with cancelled tools. Need a closing assistant.

```
assistant("", [tc1, tc2, tc3]) → tool(tc1, ok) → tool(tc2, Error)
  → **assistant("用户取消了任务，tc1 成功，tc2 失败")**
```

`[CANCELLED]` tool message is absorbed into the synthetic assistant
description. The cancelled intent (from `/stop` or cascade) is made explicit
so the LLM sees a clean summary.

### Scenario 4 — No tool_calls, final response + injection

```
assistant("最终回复") → user(新指令)
```

**No intervention needed.** `assistant → user` is already a legal sequence.

---

## Handling by Execution Count

### 1a / 2a — All tools executed

```
assistant("", [tc1, tc2]) → tool(tc1, ok) → tool(tc2, ok) → user(new)
```

**Problem**: `tool → user` — illegal role alternation.
**Fix**: Append a closing assistant before the new user.

```
assistant("", [tc1, tc2]) → tool(tc1, ok) → tool(tc2, ok)
  → **assistant("tc1、tc2 已完成。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")**
  → user(new)
```

Scenario 2 variant (with text content):
```
assistant("回复", [tc1, tc2]) → tool(tc1, ok) → tool(tc2, ok)
  → **assistant("回复内容\n\ntc1、tc2 已完成。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")**
  → user(new)
```

### 1b / 2b — Partial execution

```
assistant("", [tc1, tc2, tc3]) → tool(tc1, ok) → ...
```

**Fix**: Strip unexecuted `tool_calls` from the original assistant. Remove
fake `[BYPASSED]` tool messages. Append a closing assistant with pending
items.

```
**assistant("", [tc1])** → tool(tc1, ok)
  → **assistant("tc1 已完成。我打算晚一点再执行 tc2、tc3。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")**
  → user(new)
```

`tc2`, `tc3` never appear in the conversation — they never existed from the
LLM's perspective. The LLM sees "tc2、tc3" as pending items it queued
itself, and can decide whether to resume them based on the user's new
message.

Scenario 2 variant (with text content):
```
**assistant("回复内容", [tc1])** → tool(tc1, ok)
  → **assistant("回复内容\n\ntc1 已完成。我打算晚一点再执行 tc2、tc3。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")**
  → user(new)
```

### 1c / 2c — Zero tools executed

```
user(original) → assistant("", [tc1, tc2]) → ... → user(new)
```

No tool results exist. The entire tool chain is replaced with a closing
assistant describing the pending plan.

```
assistant("我打算晚一点再执行 tc1、tc2。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")
  → user(new)
```

Scenario 2 variant (with text content):
```
assistant("回复内容\n\n我打算晚一点再执行 tc1、tc2。用户发送了新消息，请根据他的意思优先处理。如果有新任务可以并行处理。")
  → user(new)
```

The preceding message (user or tool) is followed by this closing assistant,
so the sequence is always `... → assistant → user(new)`.

---

## Implementation Notes

- **Only the `was_interrupted` block** in `runner.py` (~lines 371-403) needs
  changing.
- **`strip_bypassed_tool_messages`** becomes a no-op in the new flow (no
  `[BYPASSED]` messages generated), but keeping it is harmless.
- **`backfill_missing_tool_results`** also becomes no-op since all declared
  `tool_calls` either have real results or are stripped.
- The closing assistant message is **structural**: it only restates facts
  that already exist in the conversation (which tools completed, what was
  planned). No new information is fabricated.
- No changes needed to `drop_orphan_tool_results`, `backfill`, or
  `split_thinking_messages` — they operate correctly on the cleaned sequence.
- **No intent detection needed**: The closing assistant is always fact-only.
  The LLM interprets the user's intent from the new message naturally.
