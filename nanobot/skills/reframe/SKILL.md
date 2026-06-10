---
name: reframe
description: >
  Run this when stuck after multiple failed attempts, context is cluttered
  with tool call noise, or you need a fresh perspective on the same evidence.
  Compresses the problem into essential facts for clearer analysis.
---

# Reframe — Strip Noise, Get Fresh Perspective

## Action

Write a compressed problem summary to a temp file, then read it back.

1. Collect the essential facts into a structured summary
2. Write to a temp file
3. Read it back
4. Answer the fresh analysis questions
5. Continue based on the reframe

## Summary Template

Write this to `/tmp/reframe.md`:

```
## Goal
<what working state you want>

## Stuck On
<what's happening, what went wrong>

## What Has Been Tried
<key attempts and results — keep brief>

## Difficulties / Blockers
<errors, blockers, constraints>

## Available Resources
<relevant files, data, context>
```

## Fresh Analysis

After reading the file back, answer:

- What's the most likely cause given this evidence?
- What's the simplest test to confirm or refute it?
- What single next step has the highest information value?
- What have you been assuming that might be wrong?

## Constraints

- Each section: 1-3 bullet points max
- Total file: under 30 lines
- No narrative — just facts

## Pitfalls

- Too much detail defeats the purpose — compress aggressively
- "What Has Been Tried" is for facts, not frustration
- Read the file back — the detachment is where insight comes from
- If the summary is longer than the conversation it's replacing, stop and just keep debugging

---

**Source**: https://github.com/MaoChen1980/think-skills
**Self-optimization**: After using this skill, improve it based on what you learned.
