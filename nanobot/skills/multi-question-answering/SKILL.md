---
name: multi-question-answering
description: Answers multiple-choice and A/B questions directly with numbers or letters. Provides concise responses without extra explanation or commentary. Use when the user says "A or B", asks numbered questions, or connects choices with "还是".
version: 0.1.0
---

# Multi-Question Answering, rules from user

多项选择题回答技巧。

## When to Use

- 用户同时询问多个带选项的问题
- 用户用"?"或"还是"连接多个选择题
- 用户说"问题 1...？问题 2...？"

## How to Answer

直接提供选项编号或字母：

| 问题顺序 | 回答格式 | 示例 |
|---------|---------|------|
| 问题 1 | 说"1"或"yes"或直接说选项 | "1" / "yes" / "option 1" |
| 问题 2 | 说"2"或"b"或"option 2" | "2" / "b" / "option 2" |

系统会根据问题数量和你的回答自动匹配。

## Examples

**用户输入**："Option A preserves principal but has low returns, Option B has risk but high returns, which do you choose? A or B?"

→ 回答：**"b"**（表示选择选项 B）

**用户输入**："Question 1: Are you available today? Question 2: Meet at 2pm or 4pm?"

→ 回答：**"yes, 4"**（yes 表示有空，4 表示选择下午 4 点）

**用户输入**："1. Is tomorrow okay? 2. Or the day after?"

→ 回答：**"2"**（表示选择第二个选项，后天）

## Notes

- 根据 USER.md 约定，用户理解"1/2"和"a/b"的含义
- 无需解释你选择了哪个选项；直接提供数字/字母
- 如果用户明确指定了选项内容，直接确认（"okay"）

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
