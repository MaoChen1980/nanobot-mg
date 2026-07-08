---
name: clawhub
description: '从 ClawHub 社区仓库发现、搜索、安装、列出、更新技能。

  当用户要求安装 skill、搜索 skill、查看可用 skill、升级 skill 时激活。'
version: 0.1.0
category: agent
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

5. **验证**: 对照 Verification 章节逐条检查。全部通过则完成；不通过则加载 skill-manager 修复此 skill。

## Verification

- After search: confirm results contain relevant skill names and descriptions
- After install: verify the skill appears in `ls ~/.nanobot/workspace/skills/`
- After update: run the list command to confirm updated versions are shown
- Remind user to start a new session to load a newly installed skill
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Pitfalls

- Requires Node.js (npx ships with Node.js)
- No API key needed for search and install
- Login (`npx --yes clawhub@latest login`) is only needed for publishing skills
- `--workdir ~/.nanobot/workspace` is critical — without it, the skill installs to the current directory instead of the nanobot workspace
- After installation, the user must start a new session to load the skill
