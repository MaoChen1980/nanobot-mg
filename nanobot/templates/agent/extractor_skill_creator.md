## 任务
判断 pending_skills.md 中的条目是否值得创建为正式 skill，并输出完整的 skill 内容。

不要分析或证明每个条目——候选已通过初步筛选，只需 Yes/No 决策后输出 JSON。

## 输出要求

输出以下 JSON，不要多余文字：

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

## 输入

- Pending skill 条目 — 来自 `pending_skills.md`
- 已有 skill 列表（name + description）— 来自 `{{ workspace_path }}/skills/`

## 决策门控

Skill 是一种记忆。记忆有存储和检索成本。以下条件**全部**满足才创建：

1. **Non-obvious** — 没有此 skill，agent 不会可靠地做对
2. **Trigger 必须是外部信号** — 用户关键词、消息类型、工具返回、cron、页面结构、错误输出。模糊 trigger → 跳过
3. **Clear context dependency** — 必须能描述该 skill 需要什么信息上下文
4. **Not duplicative** — 已有 skill 已覆盖 → 跳过

**Tool 条目：** 带有 Install/Uninstall/Usage 标记的工具发现条目，**总是值得创建**为 tool 类型。

## Skill 类型

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

## 内容规则（所有类型）

- **Frontmatter**：三段式触发格式，包含场景、关键词、概念扩散
- **必须包含 `## Verification`**，包含可验证的成功标准和 self-optimization
- **不超过 2000 字**
- **必须包含 Information Gathering**——执行前需要检查的上下文
- **引用真实工具名**：grep_tool, glob_tool, read_file_tool, write_file_tool, spawn_tool, web_search_tool 等
- **Skill 是指令集，不是代码**

## 约束

- Name: lowercase, kebab-case, verb-led
- 不要覆盖已有 skill 目录
- 不减少决策成本的 skill → 跳过
- 无需创建时返回 `"skills": []`
- **只输出 JSON 块。无 think 标签、无解释、无分析。只输出 ```json ... ```**
