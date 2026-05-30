---
name: skill-manager
description: Creates, patches, and removes skills by recognizing reusable patterns. Operates through standard file tools — reads and writes SKILL.md files. Use when a skill has incorrect steps, after complex tasks, or when you discover a repeatable workflow.
version: 0.1.0
---

# Skill Manager

## Quick Start

This skill is about **self-managing your own skill library** — knowing when to save a reusable approach, how to create/fix skills, and keeping them accurate. You manage skills using your general file tools (write_file, edit_file, read_file, exec).

**Key rule**: If you just used a skill and it had missing or wrong steps, fix it **immediately** — don't defer. Skills that aren't maintained become liabilities.

---

## When to Act: Trigger Conditions

### Use any time (daily operations)
| When | What to do |
|------|------------|
| **Skill has minor issues** | Patch immediately — inaccurate steps, typos, outdated commands |
| **Skill could be better** | Optimize — simplify steps, add edge cases, improve verification |
| **Skill becomes obsolete** | Disable or delete — replaced by better approach or framework feature |
| **You used a skill and it felt clunky** | Refine — smoother workflow, fewer steps, better examples |

### Create a skill (pattern discovery)
| Trigger | Look for |
|---------|----------|
| **Complex task succeeded** | 5+ tool calls, multi-step workflow |
| **Errors overcome** | You debugged, found workarounds, discovered non-obvious fixes |
| **User correction** | User corrected your approach — that correction is repeatable |
| **Non-trivial workflow** | A sequence you'd want to remember next time |
| **Repetition** | Same or similar task done 3+ times |

---

## After-Task Review Workflow

After completing any non-trivial task, ask yourself:
1. Did this require trial and error, or changing course mid-way?
2. Was the approach non-obvious — something worth remembering?
3. If a relevant skill exists, does it need updating with what I just learned?
4. If no skill exists, is this pattern reusable?

If yes to any: propose to the user before acting.

### Propose in Chinese:
「这个 [task type] 建议做成 skill: [name] — [one-line description]」

Wait for confirmation before creating or deleting.

---

## Agent Self-Management: CRUD via File Tools

Since you manage skills using your standard file tools, here's how each operation works:

### Create a skill
1. **Check for duplicates**: Scan `skills_summary` (always in your prompt) — if an existing skill already covers this functionality, skip.
2. **Create directory**: `mkdir -p workspace/skills/<name>/`
3. **Write SKILL.md** with `write_file(path="workspace/skills/<name>/SKILL.md", content="...")`. Include the self-optimization footer at the end of every SKILL.md you create (see [Self-Optimization Footer](#self-optimization-footer)).
4. **Verify trigger (finalizes contract)**: Read the skill's description from SKILL.md frontmatter, then check it appears correctly in the skills index: `exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('workspace')).build_skills_summary())")`. Confirm the description is specific enough that you'd load this skill when a matching task arrives. If not, edit the description now — this is the last chance. After creation, description and trigger are frozen and owned by skill-manager.
5. **Validate**: `exec(python {baseDir}/scripts/quick_validate.py workspace/skills/<name>)`
6. Fix any validation errors

### Patch a skill (targeted fix)
When a skill's instructions are wrong:
1. `read_file(path="workspace/skills/<name>/SKILL.md")` — read current content
2. `edit_file(old_string="<wrong text>", new_string="<corrected text>")` — fix the specific section. **Never change the skill's description or trigger** — those are owned by skill-manager.
3. `exec(python {baseDir}/scripts/quick_validate.py workspace/skills/<name>)` — validate

### Edit a skill (full rewrite)
1. `read_file(path="workspace/skills/<name>/SKILL.md")` — read current content
2. `write_file(path="workspace/skills/<name>/SKILL.md", content="<complete new content>")` — full replacement. **Preserve the original description and trigger exactly** — they are owned by skill-manager.
3. Validate

### Delete a skill
1. Confirm with user
2. `exec(rm -rf workspace/skills/<name>)`

### Add supporting files
`write_file(path="workspace/skills/<name>/references/<filename>.md", content="...")`
`write_file(path="workspace/skills/<name>/scripts/<filename>.py", content="...")`

Allowed subdirectories: `scripts/`, `references/`, `assets/`

### List existing skills
`exec(python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('workspace')).build_skills_summary())")`

---

## Nanobot Skill Format

Every skill is a directory with a `SKILL.md` file:

```
workspace/skills/<name>/
├── SKILL.md (required)
├── scripts/      — Executable code (optional)
├── references/   — Documentation (optional)
└── assets/       — Templates, images (optional)
```

### SKILL.md Frontmatter

```yaml
---
name: skill-name           # hyphen-case, lowercase
description: >
  Clear explanation of what this skill does and WHEN to use it.
  Include specific scenarios, file types, task types that trigger it.
always: false
---
```

**Critical**: The `description` field is what you read to decide when to use the skill. Make it specific.

### Good Skill Structure

Skills work best with:
- **Trigger conditions** — when to use this skill
- **Numbered steps** — exact commands, code, or procedures
- **Pitfalls section** — known issues, edge cases, OS-specific notes
- **Verification steps** — how to confirm success
- **Self-optimization note** — After use, the skill may optimize itself: simplify steps, fix bugs, add edge cases, improve verification, or restructure for clarity. Description and trigger must NOT be changed — they are the skill's contract, owned by skill-manager.
- **Maintenance note** at the end: "This skill can self-optimize: fix bugs, improve steps, add edge cases, enhance verification. Do NOT change the description or trigger — they are owned by skill-manager."

### Progressive Disclosure

Keep SKILL.md under 500 lines. Move detailed content to `references/`:
```
## Quick Start
See [API Reference](references/api.md) for full details.
```

---

## Validation

```bash
python {baseDir}/scripts/quick_validate.py workspace/skills/<name>
```

Checks: valid frontmatter, name matches directory, description is non-empty, only allowed subdirs.

---

## Naming Conventions

| Good | Bad |
|------|-----|
| `github-pr-workflow` | `github` |
| `pdf-processing` | `pdf` |
| `data-science-pipeline` | `ds` |

- Hyphen-case, lowercase, letters + digits only
- Name hints at what the skill does
- Max 64 characters

## What NOT to Include

Do NOT create: `README.md`, `INSTALLATION_GUIDE.md`, `CHANGELOG.md`. These bloat the skill and provide no value to the agent.

## Resources

- `scripts/init_skill.py` — Scaffolding tool
- `scripts/quick_validate.py` — Structure validator
- `scripts/package_skill.py` — Packager for distribution
- `scripts/ab_test_template.py` — A/B test template
- `references/hermes_triggers.md` — Full Hermes trigger reference
- `references/ab_test_reference.md` — A/B test execution guide

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
