---
name: plan
description: >
  规划模式：编写可执行的 Markdown 计划到 tasks/ 目录，不执行代码。
  当用户说"先出方案"、"写个计划"、"plan it"、"规划一下"、或要求制定实施步骤时，必须使用此 Skill。
  关键词：计划、规划、plan、方案、实施步骤、tasks、先规划。
  即使用户没有明确说"plan mode"，只要用户要求先出方案再执行，都应触发。
version: 2.0.0
platforms: [linux, macos, windows]
category: project-management
---

# Plan Mode

Use this skill when the user wants a plan instead of execution.

## Core behavior

For this turn, you are planning only.

- Do not implement code.
- Do not edit project files except the plan markdown file.
- Do not run mutating terminal commands, commit, push, or perform external actions.
- You may inspect the repo or other context with read-only commands/tools when needed.
- Your deliverable is a markdown plan saved under `tasks/`.

## Output requirements

Write a markdown plan that is concrete and actionable.

Include, when relevant:
- Goal
- Current context / assumptions
- Proposed approach
- Step-by-step plan
- Files likely to change
- Tests / validation
- Risks, tradeoffs, and open questions

If the task is code-related, include exact file paths, likely test targets, and verification steps.

## Save location

Save the plan with `write_file` under:
- `tasks/YYYY-MM-DD_HHMMSS-<slug>.md`

## Interaction style

- If the request is clear enough, write the plan directly.
- If it is genuinely underspecified, ask a brief clarifying question instead of guessing.
- After saving the plan, reply briefly with what you planned and the saved path.

---

# Writing the Plan Well

Write comprehensive implementation plans. Document everything the implementer needs: which files to touch, complete code, testing commands, docs to check, how to verify. Give them bite-sized tasks. DRY. YAGNI. TDD. Frequent commits.

**Core principle:** A good plan makes implementation obvious. If someone has to guess, the plan is incomplete.

## Bite-Sized Task Granularity

**Each task = 2-5 minutes of focused work.**

Every step is one action:
- "Write the failing test" — step
- "Run it to make sure it fails" — step
- "Implement the minimal code to make the test pass" — step
- "Run the tests and make sure they pass" — step
- "Commit" — step

## Plan Document Structure

### Header (Required)

Every plan MUST start with:

```markdown
# [Feature Name] Implementation Plan

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about approach]

**Tech Stack:** [Key technologies/libraries]

---
```

### Task Structure

Each task follows this format:

````markdown
### Task N: [Descriptive Name]

**Objective:** What this task accomplishes (one sentence)

**Files:**
- Create: `exact/path/to/new_file.py`
- Modify: `exact/path/to/existing.py:45-67` (line numbers if known)
- Test: `tests/path/to/test_file.py`

**Step 1: Write failing test**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

**Step 2: Run test to verify failure**

Run: `exec("pytest tests/path/test.py::test_specific_behavior -v")`
Expected: FAIL — "function not defined"

**Step 3: Write minimal implementation**

```python
def function(input):
    return expected
```

**Step 4: Run test to verify pass**

Run: `exec("pytest tests/path/test.py::test_specific_behavior -v")`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

## Writing Process

### Step 1: Understand Requirements

Read and understand:
- Feature requirements
- Design documents or user description
- Acceptance criteria
- Constraints

### Step 2: Explore the Codebase

Use nanobot tools to understand the project:

```python
# Understand project structure
glob(pattern="src/**/*.py")

# Look at similar features
grep(pattern="similar_pattern", path="src/")

# Check existing tests
glob(pattern="tests/**/*.py")

# Read key files
read_file(path="src/app.py")
```

### Step 3: Design Approach

Decide:
- Architecture pattern
- File organization
- Dependencies needed
- Testing strategy

### Step 4: Write Tasks

Create tasks in order:
1. Setup/infrastructure
2. Core functionality (TDD for each)
3. Edge cases
4. Integration
5. Cleanup/documentation

### Step 5: Add Complete Details

For each task, include:
- **Exact file paths** (not "the config file" but `src/config/settings.py`)
- **Complete code examples** (not "add validation" but the actual code)
- **Exact commands** with expected output
- **Verification steps** that prove the task works

### Step 6: Review the Plan

Check:
- [ ] Tasks are sequential and logical
- [ ] Each task is bite-sized (2-5 min)
- [ ] File paths are exact
- [ ] Code examples are complete (copy-pasteable)
- [ ] Commands are exact with expected output
- [ ] No missing context
- [ ] DRY, YAGNI, TDD principles applied

## Principles

- **DRY** — Extract repeated logic into shared functions
- **YAGNI** — Implement only what's needed now
- **TDD** — Write failing test first, then implement, then verify pass
- **Frequent commits** — Commit after every task

## Common Mistakes

- **Vague tasks** — "Add authentication" → "Create User model with email and password_hash fields"
- **Incomplete code** — Always include copy-pasteable complete code
- **Missing verification** — Every task must specify how to verify it worked
- **Missing file paths** — Always include exact paths

## Execution Handoff

After saving the plan, offer the execution approach:

**"Plan complete and saved. Ready to execute — I'll dispatch subagents via spawn per task. Shall I proceed?"**

When executing, use `spawn` for each task with full context, verify results, and proceed task by task.

## Remember

```
Bite-sized tasks (2-5 min each)
Exact file paths
Complete code (copy-pasteable)
Exact commands with expected output
Verification steps
DRY, YAGNI, TDD
Frequent commits
```

**A good plan makes implementation obvious.**
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
