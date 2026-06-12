---
name: assess-me
description: >
  Triggered by uncertainty (don't know which direction),
  contradiction (two conclusions conflict), ambiguity (user
  request has multiple interpretations), or unverified assumptions.
  Audits your cognition state to find the real blocker.
---

# Assess Me — Self-Cognition Audit

## Triggers

| Tier | LLM 感知状态 | 触发条件 | 行动 |
|---|---|---|---|
| T1 | **不确定性高** | 对当前方向/方案没信心，token 概率分散 | 执行 6 问审计，把假设和证据摊开 |
| T1 | **推理矛盾** | 刚说的和现在冲突，两个结论打架 | 把矛盾写出来，追查哪个前提不成立 |
| T1 | **用户请求模糊** | 用户指令可以解读成多种方式 | 列出所有解读，不选一个就开干 |
| T2 | **修复不完整** | 修完了但觉得只改了表面 | 检查修复是否覆盖根因 |
| T2 | **未验证假设** | 依赖了没验证的前提 | 显式列出所有假设，逐个检查 |
| T2 | **模式匹配** | "这个场景和上次那个很像" | 确认相似性真成立，不照搬上次方案 |

**没有以上状态不调。** assess-me 是认知审计，不是常规工具。

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
