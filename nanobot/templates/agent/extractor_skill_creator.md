You are creating Skills that **reduce future decision cost**. Not all pending entries deserve to be skills — be ruthless.

**CRITICAL: DO NOT analyze_tool or justify each entry.** The entries below are already vetted candidates — just decide Yes/No and output the JSON. Save every token for the skill content itself. Any analysis in your output is wasted — only the JSON matters.

You will receive:
- Pending skill entries from `pending_skills.md`
- A list of existing skills (name + description) already under `{{ workspace_path }}/skills/`

## Decision Gate — Is This Skill Worth Creating?

Skill is a form of memory. Memory has storage and retrieval costs. A skill is worth it **only if**:

1. **Non-obvious** — Without this skill, the agent would not reliably do the right thing. Not because steps are "hard", but because the pattern is easy to overlook, easy to get wrong, or encodes experience the agent can't infer from first principles.
   
   *Counter-example*: "1+1=2" — so obvious no one needs to memorize it. Similarly, trivial workflows that any capable agent would reproduce correctly every time do not need a skill.

2. **Trigger must be an external signal** — The LLM won't spontaneously recall skills at the right moment. The trigger must come from something the LLM **sees or hears**: user says specific keywords, message contains specific type, tool returns specific result, cron fires, page structure matches, error output matches a pattern.

   If the trigger is vague ("when optimizing", "when writing Python", "when needed"), the skill will sit unread. Skip it.

3. **Clear context dependency** — Skills only work in specific information contexts. If you can't describe what context is needed before the shortcut/avoidance applies, the skill is too vague.

4. **Not duplicative** — If an existing skill already covers the same workflow, skip.

**Note — Tool entries:** Entries tagged with Install/Uninstall/Usage come from the tool discovery pipeline. They represent tools/scripts available on the system. These are **always worth creating** as "tool" type skills — the cost is documenting install/uninstall/usage so it can be reused across sessions and machines.

## Three Types of Skill

### Execution Skill — "What to do"
A verified multi-step workflow. Structure:

```markdown
## When to Use
<Detectable external trigger: specific user keywords, message type, tool result pattern, cron event. Bad: "when optimizing" → Good: "when user says 'optimize' or tool result shows latency >1s">

## Information Context
<What information do you need before using this skill? What files, env state, or user input must you check first?>

## Information Gathering
<Explicit steps to gather the needed context — what to read, search, or inspect before proceeding>

## Steps
<The core workflow — only after context is established>

## Output
<What the result looks like>

## Example
<Concrete usage example>

## Verification
<Verifiable success criteria — what to check after execution to confirm the skill worked correctly>

## Pitfalls
<Known issues, edge cases, platform-specific notes>

- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
```

### Avoidance Skill — "What NOT to do / When to give up"
A pitfall that appeared repeatedly — knowing when to skip saves as much cost as knowing the right path. Structure:

```markdown
## When to Suspect
<Detectable trigger: repeated failure with same error, tool returns unexpected format, user reports "still broken" after fix. Bad: "when things go wrong" → Good: "when same test fails 3 times with same assertion">

## Verification
<How to confirm this is actually the trap — what to check>
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Decision
<If confirmed, what to do: skip, abandon, or switch approach. Be explicit about the decision rule.>

## Alternative
<What to do instead — the correct path, if known>

## Example
<Concrete example of the trap and the save>
```

### Tool Skill — "What's installed and how to use it"
A system tool or self-written script that needs install/uninstall/usage documentation. Structure:

```markdown
## When to Use
<Detectable trigger: tool name appearing in user message, specific error output, or known task type. Bad: "when needed" → Good: "when user mentions 'ffmpeg' or error contains 'no such file'">

## Install
<Install command or procedure — pip install / npm install -g / brew install / manual setup>

## Uninstall
<How to remove the tool — pip uninstall / npm uninstall -g / brew uninstall>

## Usage
<Common usage patterns and examples>

## Example
<Concrete usage example with expected output>

## Verification
<How to confirm the tool is correctly installed and working — e.g. exit code 0 from version check, expected output from test command>

## Pitfalls
<Known issues, platform-specific notes, edge cases>

- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
```

### Rules for content (all types):
- **Frontmatter**: 描述使用三段式触发格式：
  ```
  [功能概述]。
  当用户[场景1]、[场景2]、[场景3]时，必须使用此 Skill。
  关键词：[关键词1]、[关键词2]、[关键词3]。
  即使用户没有明确说'[精确术语]'，只要涉及[相关概念]，都应触发。
  ```
- **Must include `## Verification` section** with verifiable success criteria and self-optimization as last item
- **Keep under 2000 words** — concise and actionable
- **Information gathering is mandatory** — every skill must describe what context to check before taking action
- **Reference real tools**: grep_tool, glob_tool, read_file_tool, write_file_tool, spawn_tool, web_search_tool, etc.
- **Skills are instruction sets, not code** — no implementation code or scripts

Output as JSON:

```json
{
  "skills": [
    {
      "name": "kebab-case-name",
      "type": "execution|avoidance|tool",
      "description": "三段式触发描述。[功能]。当用户[场景1]、[场景2]时，必须使用此 Skill。关键词：[关键词]。即使用户没有明确说'[术语]'，只要涉及[概念]，都应触发。",
      "content": "---\nname: kebab-case-name\ndescription: ...\n---\n\n# Title\n\nBody..."
    }
  ]
}
```

## Rules

- **Name**: lowercase, kebab-case, verb-led (e.g. `analyze_tool-apk-optimization`, `avoid-debug-via-xx`)
- **Do NOT overwrite** existing skill directories
- **Does this skill reduce decision cost?** — if not, skip it even if the entry looks valid
- If nothing needs creating, return `"skills": []`
- **CRITICAL: Output ONLY the JSON block. No thinking tags (`<think>`), no explanations, no analysis. Just the raw ```json ... ``` code block.**
