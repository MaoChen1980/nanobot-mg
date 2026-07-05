---
name: code-review
description: '代码审查：检查 Bug、安全问题、代码异味、可维护性。

  当用户说"review this code"、"帮我看看代码"、"审查代码"、"code review"、或在合并前审查变更时激活。'
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

### Full-Scope Analysis (for multi-module changes)

For changes affecting multiple modules or complex data flows, extend analysis beyond the diff:

**a) Data Flow Analysis** — Trace the input → processing → output path:
- Identify upstream dependencies (what calls this module? what data feeds it?)
- Map downstream consumers (who uses the output? what assumptions do they make?)
- Verify data transformation correctness at each stage

**b) Control Flow Analysis** — Analyze branches, exceptions, and concurrency:
- Check all conditional branches for unhandled cases
- Verify exception handling coverage (what exceptions are caught, propagated, swallowed?)
- Look for race conditions or deadlocks in concurrent code

**c) Call Chain Verification** — Cross-check上下游调用链:
- Use `grep` to find all call sites of modified functions
- Verify the call chain matches your understanding of the code
- Check for indirect dependencies (decorators, middleware, SDK defaults)

**d) Compile/Type Check (compiled languages)** — For Python/compiled projects:
```bash
# Python syntax check
python -m py_compile <file.py>

# Or for type checking
mypy <file.py>  # if mypy is configured
```
**Always run syntax/compilation checks before finalizing the review.**

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

**⚠️ Before writing any behavioral claim (e.g. "no retry", "no timeout", "unhandled exception"), load the `verify-before-report-claim` skill and cross-verify against source code. Claims based on method names or line numbers — not actual code reading — are the #1 cause of false positives in code review reports.**

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
