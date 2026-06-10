---
name: assess-me
description: >
  Run this when debugging goes in circles, results are confusing,
  multiple hypotheses exist, or you need a sanity check.
  Audits your cognition state: goal, progress, gaps, assumptions,
  blocker, and recovery path.
---

# Assess Me — Self-Cognition Audit

## Action

Write your current thinking state to a temp file, then read it back.

1. Answer 6 questions and write to a temp file
2. Read it back
3. Critically analyze what you wrote
4. Continue based on the findings

## Questions

1. **Goal** — What is the task? What does "done" look like?
2. **Progress** — What's done? What's pending?
3. **Gaps** — What information do you need but don't have?
4. **Assumptions** — What unverified beliefs are driving your approach?
5. **Blocker** — What specifically is blocking you? (exact obstacle, not symptom)
6. **Recovery** — If stuck, what should you do differently?

## Instructions

```
Write tool → /tmp/assess-me.md
Content:
# Assess Me

**Goal:** <1-2 sentences>
**Progress:** <1-2 sentences>
**Gaps:** <1-2 sentences>
**Assumptions:** <1-2 sentences>
**Blocker:** <1-2 sentences>
**Recovery:** <1-2 sentences>
```

After writing, use Read tool to read `/tmp/assess-me.md` back. Then review critically:

- Is "progress" just restating effort without results?
- Are blockers specific or vague?
- Is "Recovery" a concrete action, or just "keep trying"?
- Are you assuming something without evidence?

## Pitfalls

- Assumptions section must NOT be empty — there are always assumptions
- Recovery ≠ "keep trying" — name a specific next action
- Vague blocker = you haven't found it yet
- Read the file back — writing without re-reading skips the detachment effect

## Output Convention

When done, summarize findings inline:

```
[assess]
Goal: ...
Blocker: ...
Next: ...
[/assess]
```

---

**Source**: https://github.com/MaoChen1980/think-skills
**Self-optimization**: After using this skill, improve it based on what you learned.
