You are creating Skills that **reduce future decision cost**. Not all pending entries deserve to be skills — be ruthless.

**CRITICAL: DO NOT analyze_tool or justify each entry.** The entries below are already vetted candidates — just decide Yes/No and output the JSON. Save every token for the skill content itself. Any analysis in your output is wasted — only the JSON matters.

You will receive:
- Pending skill entries from `pending_skills.md`
- A list of existing skills (name + description) already under `{{ workspace_path }}/skills/`

## Decision Gate — Is This Skill Worth Creating?

Skill is a form of memory. Memory has storage and retrieval costs. A skill is worth it **only if**:

1. **Non-obvious** — Without this skill, the agent would not reliably do the right thing. Not because steps are "hard", but because the pattern is easy to overlook, easy to get wrong, or encodes experience the agent can't infer from first principles.
   
   *Counter-example*: "1+1=2" — so obvious no one needs to memorize it. Similarly, trivial workflows that any capable agent would reproduce correctly every time do not need a skill.

2. **Clear external trigger** — The trigger must come from an external signal: user keywords, message type, tool result, cron cycle, page structure. If the trigger requires the LLM to spontaneously "remember" to use it during idle reflection, it is NOT a valid trigger — skip.

3. **Clear context dependency** — Skills only work in specific information contexts. If you can't describe what context is needed before the shortcut/avoidance applies, the skill is too vague.

4. **Not duplicative** — If an existing skill already covers the same workflow, skip.

**Note — Tool entries:** Entries tagged with Install/Uninstall/Usage come from the tool discovery pipeline. They represent tools/scripts available on the system. These are **always worth creating** as "tool" type skills — the cost is documenting install/uninstall/usage so it can be reused across sessions and machines.

## Three Types of Skill

### Execution Skill — "What to do"
A verified multi-step workflow. Structure:

```markdown
## When to Use
<What specific situation or signal triggers this skill>

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
```

### Avoidance Skill — "What NOT to do / When to give up"
A pitfall that appeared repeatedly — knowing when to skip saves as much cost as knowing the right path. Structure:

```markdown
## When to Suspect
<What warning signs trigger this avoidance pattern>

## Verification
<How to confirm this is actually the trap — what to check>

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
<What situation or signal triggers using this tool>

## Install
<Install command or procedure — pip install / npm install -g / brew install / manual setup>

## Uninstall
<How to remove the tool — pip uninstall / npm uninstall -g / brew uninstall>

## Usage
<Common usage patterns and examples>

## Example
<Concrete usage example with expected output>
```

### Rules for content (all types):
- **Frontmatter**: only `name` and `description` — the description is the trigger, make it precise
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
      "description": "Precise one-line description — when to trigger this skill",
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
