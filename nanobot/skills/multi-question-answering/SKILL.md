---
name: multi-question-answering
description: Answers multiple-choice and A/B questions directly with numbers or letters. Provides concise responses without extra explanation or commentary. Use when the user says "A or B", asks numbered questions, or connects choices with "还是".
version: 0.1.0
---

# Multi-Question Answering, rules from user

Multi-choice question answering technique.

## When to Use

- User asks multiple questions with options simultaneously
- User connects multiple choice questions with "?" or "还是" (or)
- User says "Question 1...? Question 2...?"

## How to Answer

Provide option numbers or letters directly:

| Question Order | Answer Format | Example |
|---------|---------|------|
| Question 1 | Say "1" or "yes" or the option directly | "1" / "yes" / "option 1" |
| Question 2 | Say "2" or "b" or "option 2" | "2" / "b" / "option 2" |

The system automatically matches based on the number of questions and your response.

## Examples

**User input**: "Option A preserves principal but has low returns, Option B has risk but high returns, which do you choose? A or B?"

→ Answer: **"b"** (indicating choice of Option B)

**User input**: "Question 1: Are you available today? Question 2: Meet at 2pm or 4pm?"

→ Answer: **"yes, 4"** (yes means available, 4 means choosing 4pm)

**User input**: "1. Is tomorrow okay? 2. Or the day after?"

→ Answer: **"2"** (indicating choice of the second option, the day after)

## Notes

- According to USER.md conventions, users understand the meaning of "1/2" and "a/b"
- No need to explain which option you chose; just provide the number/letter
- If user explicitly specifies the option content, just confirm ("okay")

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
