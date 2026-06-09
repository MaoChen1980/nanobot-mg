---
name: github
description: Trigger when the user asks to check pull requests, review CI status, list issues or workflow runs, search code on GitHub, or query the GitHub API. Requires gh CLI with GitHub authentication.
version: 0.1.0
---

# GitHub Skill

Interact with GitHub using the `gh` CLI. When not inside a git directory, always specify `--repo owner/repo` or use a full URL.

## When to Use

- Check PR status, CI checks, and workflow runs
- List and view GitHub issues
- Query GitHub API for data not available through subcommands
- Search code across repositories
- Retrieve structured JSON output from any GitHub resource

## Steps

1. **Install gh CLI** if not present:
   - macOS: `brew install gh`
   - Linux: `apt install gh` or `brew install gh`
   - Windows: `winget install GitHub.cli`

2. **Authenticate** if needed: `gh auth login`

3. **Check PR CI status**:
   ```bash
   gh pr checks 55 --repo owner/repo
   ```

4. **List recent workflow runs**:
   ```bash
   gh run list --repo owner/repo --limit 10
   ```

5. **View a specific run and check for failures**:
   ```bash
   gh run view <run-id> --repo owner/repo
   ```

6. **View only failed step logs**:
   ```bash
   gh run view <run-id> --repo owner/repo --log-failed
   ```

7. **Query the GitHub API for custom data** — use `gh api` for data not available through subcommands:
   ```bash
   gh api repos/owner/repo/pulls/55 --jq '.title, .state, .user.login'
   ```

8. **Use JSON output for structured data** — most commands support `--json` with `--jq` filtering:
   ```bash
   gh issue list --repo owner/repo --json number,title --jq '.[] | "\(.number): \(.title)"'
   ```

## Verification

- Run `gh pr checks <number> --repo owner/repo` and confirm it returns check results without errors
- Run `gh api repos/owner/repo/pulls/55 --jq '.title'` and verify a non-empty title string is returned
- Run `gh --version` to confirm the CLI is installed and authenticated

## Pitfalls

- **`--repo` flag required**: When not inside a local git clone, all commands need `--repo owner/repo` or must be run from a directory with a git remote
- **Authentication**: `gh` must be authenticated via `gh auth login` before use. Unauthenticated requests return a 401 error
- **JSON output**: Not all subcommands support `--json`; fall back to `gh api` for unsupported endpoints
- **Pagination**: Large result sets are paginated by default; use `--paginate` with `gh api` or `--limit 0` for full results

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
