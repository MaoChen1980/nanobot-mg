---
name: reframe
description: >
  Triggered by repeating loops (same approach failed 2+ times),
  context overload (too much tool noise), over-complication
  (solution getting bigger than problem), or fixing symptoms
  instead of root cause. Compresses the problem for a fresh view.
---

# Reframe — Strip Noise, Get Fresh Perspective

## Triggers

| Tier | LLM 感知状态 | 触发条件 | 行动 |
|---|---|---|---|
| T1 | **重复循环** | 试了几种方案都走不通，感觉在打转 | 把问题压成 ≤30 行事实摘要，换角度分析 |
| T2 | **上下文超载** | tool call 输出填满对话，看不清核心 | 只提取关键事实，扔掉噪声 |
| T2 | **方案过度复杂** | 方案越来越长，但核心没简化 | 剥离症状，重新定义核心问题 |
| T2 | **修复不完整** | 修了表面但感觉根因没找到 | 用压缩摘要重新检查因果链 |

**区别于 assess-me 和 debug-root-cause：**
- assess-me 审计你的认知状态（不确定/矛盾）
- debug-root-cause 排查外部因果链（error/bug）
- reframe 改变问题框架（打转/噪声/复杂化）

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
