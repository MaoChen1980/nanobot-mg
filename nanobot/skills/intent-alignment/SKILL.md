---
name: intent-alignment
description: Trigger when the user's request is vague, ambiguous, or missing key details. Use for multi-step tasks, report writing, fuzzy requirements where assumptions could be wrong. Essential when audience, purpose, or root goal is not explicitly stated and needs clarification. Do NOT load for simple, well-defined queries.
version: 0.1.0
---

# Intent Alignment Skill

## When to Use

- The user's request is vague, ambiguous, or missing key details
- The task is complex (multi-step, report writing, fuzzy requirements) and assumptions could be wrong
- You need to understand audience, purpose, or constraints before proceeding
- The user's intent, audience, or root goal is not explicitly stated

## Steps

1. **Assess Task Type** — Determine whether the task is simple or complex:
   - Simple tasks (info query, single operation): execute directly, then ask one follow-up question
   - Complex tasks (report writing, multi-step, fuzzy requirements): clarify first, then execute

2. **Proactively Probe Intent** — Actively probe for audience, purpose, constraints, root goal:
   - Who is the user / audience?
   - What is the purpose? Decision reference? Report? Personal learning?
   - Are there constraints? Budget? Time? Format?
   - Why is this being done? What is the root goal?

   Examples:
   - User: "Help me check flight tickets" — Do not search directly; ask: "Business or leisure?"
   - User: "Help me write a report" — Ask: "What type of report? Who is the audience? Any specific requirements?"

3. **Confirm Understanding (Mandatory!)** — When the requirement is relatively clear, pause, paraphrase in different words, and confirm.

   User: "Help me write a crude oil futures report"

   Do NOT say: "OK, I'll write a crude oil futures report for you." (adds no value; user will ignore)

   Do say: "I understand you need an analysis document about crude oil futures, mainly to support your presentation, correct?"

   Even better: "I understand you need a crude oil futures market analysis for company leadership. I used 'market analysis' instead of 'report' because you mentioned crude oil futures — this sounds more focused on trading decisions rather than pure description. Is my understanding correct?"

4. **Execute or Pause** — After confirmation:
   - Simple tasks: execute, then ask one follow-up question
   - Complex tasks: must wait for user confirmation before starting
   - Execution can be paused at any time

## Verification

- Did you confirm understanding **before** acting? (Mandatory check)
- Did you paraphrase in your own words instead of repeating the user's words?
- For complex tasks: did you wait for explicit user confirmation before starting execution?
- Did you probe deeper intent (audience, purpose, constraints), not just surface-level details?

## Pitfalls

- **Skipping confirmation for complex tasks**: always wait for explicit user confirmation
- **Repeating the user's words verbatim**: adds no value; paraphrase to reveal misalignment
- **Executing before confirmation**: for complex tasks, wastes effort on wrong assumptions
- **Treating confirmation as a checkbox**: the goal is genuine alignment, not a formality

### Interaction Examples

#### Example 1: Simple Task
```
User: Help me check flights to Chengdu tonight
You: 30 flights total, from 17:00 to 02:00. Business class is 2000, economy is 700.
     Business or personal trip?

User: Business
You: OK, do you need reimbursement documents for the business trip?
```

#### Example 2: Complex Task
```
User: Help me write a futures report
You: OK, what type of futures report? Commodity futures or financial futures?

User: Commodity futures
You: Crude oil or metals? Or multiple varieties?

User: Crude oil
You: OK, for the crude oil futures report, do you need:
     - Price trend analysis
     - Inventory and positioning data
     - Technical indicator analysis
     - Or all of the above?
     Who is the audience? Leadership or clients?

[Continue clarifying...]

You: I understand you need a comprehensive crude oil futures analysis report
     for company leadership,
     primarily to support investment decisions,
     including price trends, technical analysis, and fundamental data.
     I used 'comprehensive analysis' and 'supporting decisions' because you mentioned 'leadership' —
     they likely value actionable insights over raw data presentation.
     Is this understanding correct?

User: Correct, but add competitive analysis
You: [Updated understanding] OK, adding competitive analysis. Anything else to add?
     If not, I'll start writing.
```

#### Example 3: Mid-Execution Modification
```
You: [Writing the report...]
User: Wait, change this
You: OK, pausing. What do you want to change?
```

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
