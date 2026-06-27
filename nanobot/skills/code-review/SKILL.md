---
name: code-review
description: "Review code for bugs, security issues, code smells, and maintainability. Use for PR review, pre-commit diff review, or when user asks 'review this code' / 'code review'."
category: code-review
---

# Code Review Skill

## When to Use

- User asks "review this code", "code review", "审查代码", "帮我看一下代码"
- Before merging a PR or branch
- After completing a batch of changes
- When asked to evaluate code quality

## Strategy

1. **Get the diff** — `exec("git diff main...HEAD")` or `exec("git diff --cached")` to see what changed
2. **Read the modified files** — Focus on the diff context; read surrounding code when needed
3. **Check each change** for:
   - Bugs (logic errors, edge cases, null safety, race conditions)
   - Security issues (injection, XSS, SSRF, hardcoded secrets, privilege escalation)
   - Code smells (duplication, over-complexity, premature abstraction, magic numbers)
   - Maintainability (naming, comments, test coverage, error handling)
   - API compatibility (breaking changes, deprecated usage, version mismatches)

## Output Format

```
## Files Reviewed
- `path/to/file.py` (lines X-Y) — what changed

## Critical (must fix)
- `file.py:42` — Issue description with clear "what" and "why"

## Warnings (should fix)
- `file.py:100` — Issue description

## Suggestions (consider)
- `file.py:150` — Improvement idea

## Summary
Overall assessment in 2-3 sentences. Include any positive observations about well-structured code.
```

Be specific with file paths and line numbers. Every finding must be traceable to actual code.

## Direct Review (small changes)

For small diffs (1-3 files, <100 lines changed), review directly:
1. `exec("git diff main...HEAD")` to get the diff
2. `read_file` to read files with changes
3. `grep` if you need to check related code
4. Output structured review

## Subagent Review (large changes)

For large PRs or complex reviews, delegate via `spawn`:

```
spawn(
  label="code-review",
  task="Review the following changes...",
  model="claude-sonnet-4-6",
  instruction="You are a senior code reviewer..."
)
```

Include the diff output and relevant file contents in the spawn task.

## Verification

- Every "Critical" finding has a specific file:line reference
- Every finding explains both *what* is wrong and *why* it matters
- Output format matches the required sections
- For spawn reviews: verify the subagent's output is complete, not truncated
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
