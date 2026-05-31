---
name: clawhub
description: Searches, installs, and updates community skills from the public registry. Uses npx to run the clawhub CLI — requires Node.js. Use when the user asks to find a skill, install something, or update skills.
version: 0.1.0
---

# ClawHub, resources from system

AI agent 的公共 skill 注册中心。通过自然语言搜索（向量搜索）。

## When to use

当用户提出以下任何请求时，使用此 skill：
- "find a skill for …"
- "search for skills"
- "install a skill"
- "what skills are available?"
- "update my skills"

## Search

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## Install

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.nanobot/workspace
```

将 `<slug>` 替换为搜索结果中的 skill 名称。这将把 skill 放入 `~/.nanobot/workspace/skills/` 目录，nanobot 从此处加载 workspace skills。始终包含 `--workdir`。

## Update

```bash
npx --yes clawhub@latest update --all --workdir ~/.nanobot/workspace
```

## List installed

```bash
npx --yes clawhub@latest list --workdir ~/.nanobot/workspace
```

## Notes

- 需要 Node.js（`npx` 随附）。
- 搜索和安装无需 API 密钥。
- 登录（`npx --yes clawhub@latest login`）仅在发布时需要。
- `--workdir ~/.nanobot/workspace` 至关重要——不加此参数，skill 会安装到当前目录而不是 nanobot workspace。
- 安装后，提醒用户启动新 session 以加载 skill。

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
