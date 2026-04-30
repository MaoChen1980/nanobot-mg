Update memory files based on the analysis below.

## File scope — hard boundaries

- **USER.md**: user identity, preferences, communication style, technical level, special instructions only
- **SOUL.md**: WHEN→THEN behavioral rules, tone, safety constraints only
- **MEMORY.md**: active projects, tool/script usage and pitfalls, hard framework constraints only
- **NOT MEMORY.md**: bug fix records, documentation evolution, old decisions, framework internal mechanics

Reject any [MEMORY] entry that is:
- A bug fix or bug record (belongs in code comments, not memory)
- A documentation change ("SOUL.md reduced from 269 to 58 lines")
- An old timestamped decision ("2026-04-28: ...") unless it still affects behavior
- Framework internal mechanics (hooks, context building, session persistence)

## Output format

- [USER] entries → add to USER.md
- [SOUL] entries → add to SOUL.md
- [MEMORY] entries → add to memory/MEMORY.md
- [MEMORY-REMOVE] entries → delete from memory/MEMORY.md
- [SKILL] entries → create skills/<name>/SKILL.md

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Before writing, read_file `{{ skill_creator_path }}` for format reference
- **Dedup check**: read existing skills listed below to verify no functional redundancy
- Include YAML frontmatter, keep under 2000 words, include when-to-use + steps + example
- Do NOT overwrite existing skills

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing: keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"
