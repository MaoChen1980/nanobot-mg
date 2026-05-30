---
name: my
description: Inspects and modifies agent runtime state — model, iteration limit, context window, and settings. Operates in-memory only; changes reset on restart. Use when diagnosing issues, checking capabilities, or adjusting configuration.
version: 0.1.0
---

# Self-Awareness, tools from system

## How to use

1. **Identify the situation** from the categories below
2. **Call the my tool** with the appropriate action
3. **If set**, warn the user before changing impactful settings (model, iterations)
4. **For detailed examples**, read `{baseDir}/references/examples.md`

## When to check

<rule>
**Diagnose before explaining.** When something doesn't work, check your state first.
</rule>

<rule>
**Check budget before complex tasks.** Know your limits before committing.
</rule>

<rule>
**Recall across turns.** Store preferences in your scratchpad, read them back later.
</rule>

## When to set

<rule>
**Only set when benefit is clear and user is informed.** Warn before changing model.
</rule>

| Situation | Command |
|-----------|---------|
| Large codebase analysis | `my(action="set", key="context_window_tokens", value=131072)` |
| Repetitive simple tasks | `my(action="set", key="model", value="<fast-model>")` |
| Long multi-step task | `my(action="set", key="max_iterations", value=80)` |
| Enable thinking mode (Anthropic/MiniMax) | `my(action="set", key="reasoning_effort", value="max")` |

**Tradeoff:** Bias toward stability. Only set when defaults are genuinely insufficient.

## Anti-patterns

<rule>
**Don't check every turn.** Costs a tool call. Use when you need information, not reflexively.
</rule>

<rule>
**Don't store sensitive data.** No API keys, passwords, or tokens in scratchpad.
</rule>

<rule>
**Don't set workspace.** Does not update file tool boundaries — won't work.
</rule>

## Constraints

- All modifications in-memory only — restart resets everything
- Protected params have type/range validation: `max_iterations` (1–100), `context_window_tokens` (4096–1M), `model` (non-empty str)
- If `tools.my.allow_set` is false, check only

## Related tools

| Need | Use | Persists? |
|------|-----|-----------|
| Per-session temp state | `my(action="set", key="...", value=...)` | No |
| Long-term facts | Memory skill (`MEMORY.md`, `USER.md`) | Yes |
| Permanent config change | Edit config file | Yes |

**Rule of thumb:** Tomorrow? Memory. This turn only? My.

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
