---
name: clawhub
description: Trigger when user wants to discover, search, install, list, or update community skills from the public ClawHub registry. Use when asked "find me a skill for X", "install skill Y", "search for skills", "list available skills", or "update/upgrade skills".
version: 0.1.0
---

# ClawHub — community skill registry

Public skill registry for AI agents. Search via natural language (vector search).

## When to Use

- User asks "find a skill for ..." or "search for skills"
- User asks "install a skill" or "what skills are available?"
- User asks "update my skills"
- User asks to list installed skills

## Steps

1. **Search** for available skills:
   ```bash
   npx --yes clawhub@latest search "web scraping" --limit 5
   ```

2. **Install** a skill by slug from search results:
   ```bash
   npx --yes clawhub@latest install <slug> --workdir ~/.nanobot/workspace
   ```
   Always include `--workdir` to install into `~/.nanobot/workspace/skills/` — the nanobot workspace loading directory.

3. **Update** all installed skills:
   ```bash
   npx --yes clawhub@latest update --all --workdir ~/.nanobot/workspace
   ```

4. **List** installed skills:
   ```bash
   npx --yes clawhub@latest list --workdir ~/.nanobot/workspace
   ```

## Verification

- After search: confirm results contain relevant skill names and descriptions
- After install: verify the skill appears in `ls ~/.nanobot/workspace/skills/`
- After update: run the list command to confirm updated versions are shown
- Remind user to start a new session to load a newly installed skill

## Pitfalls

- Requires Node.js (npx ships with Node.js)
- No API key needed for search and install
- Login (`npx --yes clawhub@latest login`) is only needed for publishing skills
- `--workdir ~/.nanobot/workspace` is critical — without it, the skill installs to the current directory instead of the nanobot workspace
- After installation, the user must start a new session to load the skill

---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
