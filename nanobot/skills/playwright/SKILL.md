---
name: "playwright"
description: >
  浏览器自动化：导航、填表、截图、数据抓取、UI 流程调试。
  当用户要求自动化浏览器操作、截取网页截图、抓取动态渲染数据、填写表单、或调试前端 UI 流程时，必须使用此 Skill。
  关键词：playwright、浏览器、截图、自动化、爬虫、headless、表单填写、页面交互。
  即使用户没有明确说"playwright"，只要涉及通过真实浏览器获取数据或操作页面，都应触发。
official: true
category: domain-specific
---

# Playwright CLI Skill

Drive a real browser from the terminal using `playwright-cli`. Use `npx` to run it directly.
Treat this skill as CLI-first automation. Do not pivot to `@playwright/test` unless the user explicitly asks for test files.

## Prerequisite check (required)

Before proposing commands, check whether `npx` is available:

```bash
exec("command -v npx >/dev/null 2>&1")
```

If it is not available, pause and ask the user to install Node.js/npm. Provide these steps verbatim:

```bash
# Verify Node/npm are installed
exec("node --version")
exec("npm --version")

# If missing, install Node.js/npm, then:
exec("npx @playwright/mcp playwright-cli --help")
```

Once `npx` is present, proceed.

## Quick start

```bash
exec('npx @playwright/mcp playwright-cli open https://playwright.dev')
exec('npx @playwright/mcp playwright-cli snapshot')
exec('npx @playwright/mcp playwright-cli click e15')
exec('npx @playwright/mcp playwright-cli fill e1 "search text"')
exec('npx @playwright/mcp playwright-cli press Enter')
exec('npx @playwright/mcp playwright-cli screenshot')
```

If the user prefers a global install, this is also valid:

```bash
exec("npm install -g @playwright/mcp@latest")
exec("playwright-cli --help")
```

But default to `npx` — no global install needed.

## Core workflow

1. Open the page.
2. Snapshot to get stable element refs.
3. Interact using refs from the latest snapshot.
4. Re-snapshot after navigation or significant DOM changes.
5. Capture artifacts (screenshot, pdf, traces) when useful.

Minimal loop:

```bash
exec('npx @playwright/mcp playwright-cli open https://example.com')
exec('npx @playwright/mcp playwright-cli snapshot')
exec('npx @playwright/mcp playwright-cli click e3')
exec('npx @playwright/mcp playwright-cli snapshot')
```

## When to snapshot again

Snapshot again after:

- navigation
- clicking elements that change the UI substantially
- opening/closing modals or menus
- tab switches

Refs can go stale. When a command fails due to a missing ref, snapshot again.

## Recommended patterns

### Form fill and submit

```bash
exec('npx @playwright/mcp playwright-cli open https://example.com/form')
exec('npx @playwright/mcp playwright-cli snapshot')
exec('npx @playwright/mcp playwright-cli fill e1 "user@example.com"')
exec('npx @playwright/mcp playwright-cli fill e2 "password123"')
exec('npx @playwright/mcp playwright-cli click e3')
exec('npx @playwright/mcp playwright-cli snapshot')
```

### Debug a UI flow with traces

```bash
exec('npx @playwright/mcp playwright-cli open https://example.com')
exec('npx @playwright/mcp playwright-cli tracing-start')
# ...interactions...
exec('npx @playwright/mcp playwright-cli tracing-stop')
```

### Multi-tab work

```bash
exec('npx @playwright/mcp playwright-cli tab-new https://example.com')
exec('npx @playwright/mcp playwright-cli tab-list')
exec('npx @playwright/mcp playwright-cli tab-select 0')
exec('npx @playwright/mcp playwright-cli snapshot')
```

## Guardrails

- Always snapshot before referencing element ids like `e12`.
- Re-snapshot when refs seem stale.
- Prefer explicit commands over `eval` and `run-code` unless needed.
- When you do not have a fresh snapshot, use placeholder refs like `eX` and say why; do not bypass refs with `run-code`.
- When capturing artifacts in this repo, use `output/playwright/` and avoid introducing new top-level artifact folders.
- Default to CLI commands and workflows, not Playwright test specs.
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
