---
name: multi-question-answering
description: Trigger when the user presents multiple-choice or A/B questions in a single prompt, asks numbered questions, or connects choices with "or" / "还是". Use for survey-style prompts, binary decisions, and multi-part questions requiring compact answer format. Provides concise responses with numbers or letters instead of full sentences. Do NOT load for open-ended questions or single-option queries.
version: 0.1.0
---

# Multi-Question Answering Skill

## When to Use

- User asks multiple questions with options simultaneously
- User connects multiple choices with "?" or "or" / "还是"
- User says "Question 1...? Question 2...?"
- Survey-style prompts requiring compact answer format

## Steps

1. **Identify Question Format** — Determine whether questions use A/B choice, numbered format, or mixed types.

2. **Extract Options** — Parse each question and its corresponding options. Map them to numbers (1, 2, 3...) or letters (A, B, C...).

3. **Select Answers** — For each question, provide the most appropriate answer using the correct format:
   - A/B choice: say "a" or "b"
   - Numbered questions: say "1" / "2" / "yes, 4" etc.
   - The system auto-matches answers to questions by position

4. **Respond Concisely** — Provide only the selected option (number or letter). No extra explanation or commentary.

## Verification

- Did you respond with only the option identifier (number/letter) without extra commentary?
- Did all questions receive a corresponding answer?
- Is the answer format correct for the question type (A/B vs. numbered)?
- If the question uses "yes/no + option" format, did you include both parts?

## Pitfalls

- **Mixed choice types**: when the same prompt contains both A/B and numbered options, answer each in its own format
- **Ambiguous questions**: if options are unclear, ask for clarification rather than guessing
- **Extra commentary**: resist the urge to explain the choice — the user expects only the identifier
- **Partial answers**: when the user asks multiple questions, answer all of them, not just the first

## Examples

**User input**: "Option A preserves principal but has low returns, Option B has risk but high returns, which do you choose? A or B?"

Answer: **"b"**

**User input**: "Question 1: Are you available today? Question 2: Meet at 2pm or 4pm?"

Answer: **"yes, 4"** (yes = available, 4 = 4pm)

**User input**: "1. Is tomorrow okay? 2. Or the day after?"

Answer: **"2"** (second option, the day after)

## Notes

- According to USER.md convention, users understand "1/2" and "a/b" format without explanation
- No need to explain which option was selected; directly provide the number/letter
- If the user explicitly specifies options, confirm with a simple "okay"

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
