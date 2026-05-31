---
name: github
description: Manages GitHub pull requests, issues, CI runs, code search, and API queries. Operates through the gh CLI — requires authentication. Use when the user asks to check PRs, review CI status, list issues, or query GitHub data.
version: 0.1.0
---

# GitHub Skill, tools from system

使用 `gh` CLI 与 GitHub 交互。不在 git 目录中时，始终指定 `--repo owner/repo`，或直接使用 URL。

## Install

- **macOS**: `brew install gh`
- **Linux**: `apt install gh` or `brew install gh`
- **Windows**: `winget install GitHub.cli`

## Pull Requests

检查 PR 的 CI 状态：
```bash
gh pr checks 55 --repo owner/repo
```

列出最近的工作流运行：
```bash
gh run list --repo owner/repo --limit 10
```

查看运行并查看哪些步骤失败：
```bash
gh run view <run-id> --repo owner/repo
```

仅查看失败步骤的日志：
```bash
gh run view <run-id> --repo owner/repo --log-failed
```

## API for Advanced Queries

`gh api` 命令可用于访问其他子命令无法获取的数据。

获取 PR 的特定字段：
```bash
gh api repos/owner/repo/pulls/55 --jq '.title, .state, .user.login'
```

## JSON Output

大多数命令支持 `--json` 进行结构化输出。可以使用 `--jq` 过滤：

```bash
gh issue list --repo owner/repo --json number,title --jq '.[] | "\(.number): \(.title)"'

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
```
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
