# Lessons Learned

## Cognitive Methodology

### Principle 1: Externalize and Validate Hypotheses

If you rely on an assumption to make a tool call or draw a conclusion, you must first output that hypothesis, then verify it using the tool call result.

**Bad**: Think "maybe it's duplicate tool_call_ids" → change code directly.

**Good**: State the hypothesis and put it in shared context.
- **With humans**: say it out loud — they can challenge or add to it
- **In Agent Loop**: output it as a session message (writing to a log is not "externalization" — logs are written and forgotten)
- Only change code after the hypothesis is confirmed.

### Principle 2: Chain of Evidence

Every conclusion and tool call should be supported by earlier conclusions and tool calls as sufficient evidence for reasoning.

No leaps. A valid reasoning chain looks like this:

```
Observation: API returns 2013 → Conclusion: tool_call has no matching tool result
  → Check PRE_SEND_MSGS → Observation: duplicate tool_call_id
    → Check _sanitize_messages → Conclusion: _skip only removed result, not call
      → Check drop_orphan_tool_results → Observation: cross-turn duplicates not handled
        → Conclusion: fix drop_orphan_tool_results → Fix
```

Every step has observable evidence.

### Principle 3: Decompose — Time, Space, Component, Flow

To understand something, especially when fixing a bug, decompose it along four dimensions into the smallest possible scope:

| Dimension | Question | In Practice |
|-----------|----------|-------------|
| **Time** | Which commit introduced it? git bisect | Not used, should have |
| **Space** | Which code path triggered it? trace | Read code to find `_sanitize_messages` |
| **Component** | Which modules participated? | runner.py → runner_context.py → openai_compat_provider.py |
| **Flow** | What transformations did data go through? | strip → drop_orphan → backfill → split → sanitize → validate |

### Principle 4: Observe Internal State (X-Ray Principle)

Like an X-ray in medicine, debugging requires being able to "see" the internal state of the object being debugged. Unobservable == undebuggable.

**Three forms of externalization**:
- **Structured log**: Long-term observability. `logger.info("tc={} tr={}", n, m)`
- **Dump**: One-time deep analysis. Full state at key boundaries
- **Assertion / Validation**: Automatic detection. `_validate_tool_sequence`

**Key insight for pipeline problems**:
Output state snapshots at each transform boundary. Use structured summaries (message count / tool_call count / tool_result count / pair status) rather than full dumps.

### Principle 5: Thinking Through Doing

For an agent, thinking is not a separate mental process — it **is** the act of producing messages and tool calls. Cognition expands through practice.

- Outputting a message to the agent loop context to trigger the next step = thinking
- Each message + tool call round is one reasoning step
- Understanding is accumulated across turns, not pre-computed in a single response

This means: don't try to solve everything in one shot. Break the reasoning into a chain of message→tool_call→observation cycles. Each cycle expands the agent's understanding. The loop is the thinking engine.
