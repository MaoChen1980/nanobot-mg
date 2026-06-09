---
name: my
description: Trigger when diagnosing agent behavior issues, checking capabilities (model, iteration limit, context window), or adjusting in-memory runtime configuration. Use before complex tasks to check budget, or after unexpected behavior to inspect state. Do NOT load for permanent config changes or workspace settings.
version: 0.1.0
---

# Self-Awareness Skill

## When to Use

- Diagnosing why the agent behaves unexpectedly
- Checking current capability limits before starting a complex task
- Adjusting model, iteration limit, or context window temporarily
- Reading or writing to scratchpad for cross-turn memory
- Verifying current settings before reporting a problem

<rule>
**Diagnose before explaining.** When something goes wrong, check your state first.
</rule>

<rule>
**Check budget before complex tasks.** Understand your limits before committing.
</rule>

<rule>
**Cross-turn memory.** Store preferences in scratchpad and read back later.
</rule>

## Steps

1. **Identify Scenario** — Match your situation to one of the categories in the table below under "When to Set".

2. **Call `my` tool** — Pass the appropriate action:
   - To check state: `my(action="get")` or `my(action="get", key="...")`
   - To modify state: `my(action="set", key="...", value=...)`
   - To store data: `my(action="set", key="scratchpad", value=...)`

3. **Warn Before Destructive Changes** — When changing model or iterations, warn the user first.

4. **Review Examples** — Read `{baseDir}/references/examples.md` for detailed usage patterns.

## When to Set

<rule>
**Only set when the benefit is clear and the user is informed.** Warn before changing model.
</rule>

| Scenario | Command |
|----------|---------|
| Large codebase analysis | `my(action="set", key="context_window_tokens", value=131072)` |
| Repetitive simple tasks | `my(action="set", key="model", value="<fast-model>")` |
| Long multi-step task | `my(action="set", key="max_iterations", value=80)` |
| Enable thinking mode (Anthropic/MiniMax) | `my(action="set", key="reasoning_effort", value="max")` |

**Tradeoff:** Favor stability. Only set when defaults are truly insufficient.

## Verification

- Did the state change as expected? (Check via `my(action="get")` before and after)
- For destructive changes (model, iterations): was the user warned before applying?
- Did you confirm the new value is within allowed bounds? (e.g., max_iterations 1-100, context_window_tokens 4096-1M)
- Is the change appropriate for the scenario, not just a habitual check?

## Pitfalls

- **Do not check every turn**: consumes a tool call. Use only when information is needed, not as a habit
- **Do not store sensitive data**: no API keys, passwords, or tokens in scratchpad
- **Do not set workspace**: does not update file tool boundaries — ineffective
- **Excessive modification**: avoid setting multiple values unnecessarily; each change carries risk
- **Forgetting changes are ephemeral**: all modifications are in-memory only and reset on restart

## Constraints

- All modifications are in-memory only — reset on restart
- Protected parameters have type/range validation: `max_iterations` (1-100), `context_window_tokens` (4096-1M), `model` (non-empty string)
- If `tools.my.allow_set` is false, only inspection is possible

## Related Tools

| Need | Use | Persistent? |
|------|-----|-------------|
| Single-session temporary state | `my(action="set", key="...", value=...)` | No |
| Long-term facts | Memory skill (`MEMORY.md`, `USER.md`) | Yes |
| Permanent configuration | Edit config files directly | Yes |

**Rule of thumb:** Still needed tomorrow? Use Memory. Only this turn? Use My.

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
