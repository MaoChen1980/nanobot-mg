# Long-term Memory

This file stores important information that should persist across sessions.
It is read by the agent on every session start and updated as new information emerges.
Keep it lean — put details in history.jsonl and use `recall` to retrieve them.

## Naming & Environment

- Project nicknames and shorthand conventions
- Model, context window, and hardware specifics
- OS-specific constraints that affect tool behavior

## User Preferences (always active)

- Decision priorities (correctness vs speed vs features)
- Communication preferences
- Code/work style conventions

## Active Projects

- **Project A**: one-line description and key paths
- **Project B**: one-line description and key paths

## Self-Installed Tools & Hooks

- Tools: workspace/tools/ scripts and their capabilities
- Hooks: workspace/hooks/ agents and what they monitor
- Always document new hooks here — they don't hot-reload

## Framework Mechanisms

- SESSION.md: auto-written by hook after each turn, first 3 lines injected on restart
- HEARTBEAT.md: cross-session task tracking, agent auto-advances on restart
- Context health: context_monitor hook writes .context_health.md signals
- memory/ structure: MEMORY.md (main), goals.md, capability.md, process-log.md, history.jsonl

## Framework Constraints (hard limits)

- Execution model: Plan-Execute (one round-trip script tree), not ReAct (no multi-pass)
- Design philosophy: thin framework, LLM-managed decisions and context
- Session reset: requires platform support, agent cannot self-reset
- Test division: LLM writes tests → framework runs → framework judges passes

## Known Bugs & Workarounds

| Bug | Workaround | Status |
|-----|-----------|--------|
| (Describe) | (How to avoid) | (Fixed? Open?) |

## Document Evolution Log

Track major changes to bootstrap files so the agent can see its own growth:
- (Date): SOUL.md restructured from prose to WHEN→THEN rules
- (Date): TOOLS.md added Known Failures table

## Decision Log

Recent architectural or process decisions that affect future behavior:
- (Date): (Decision summary)

## Quick Reference

- Detailed memory: `recall <keyword>` or `grep memory/history.jsonl`
- Past conversations: `grep memory/history.jsonl`
- This file: current active info + hard constraints only; details via recall
