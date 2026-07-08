---
name: github-auth
description: 'GitHub 认证设置：HTTPS Token、SSH 密钥、gh CLI 登录。

  当用户需要配置 GitHub 访问权限、认证失败报错、或首次使用 GitHub 相关 skill 时激活。'
version: 1.1.0
platforms:
- linux
- macos
- windows
category: project-management
---

# GitHub Authentication Setup

This skill sets up authentication so the agent can work with GitHub repositories, PRs, issues, and CI. It covers two paths:

- **`git` (always available)** — uses HTTPS personal access tokens or SSH keys
- **`gh` CLI (if installed)** — richer GitHub API access with a simpler auth flow

## Detection Flow

When a user asks you to work with GitHub, run this check first:

```bash
exec("git --version")
exec("gh --version 2>/dev/null || echo 'gh not installed'")
exec("gh auth status 2>/dev/null || echo 'gh not authenticated'")
exec("git config --global credential.helper 2>/dev/null || echo 'no git credential helper'")
```

**Decision tree:**
1. If `gh auth status` shows authenticated → you're good, use `gh` for everything
2. If `gh` is installed but not authenticated → use "gh auth" method below
3. If `gh` is not installed → use "git-only" method below (no sudo needed)

---

## Method 1: Git-Only Authentication (No gh, No sudo)

This works on any machine with `git` installed. No root access needed.

### Option A: HTTPS with Personal Access Token (Recommended)

This is the most portable method — works everywhere, no SSH config needed.

**Step 1: Create a personal access token**

Tell the user to go to: **https://github.com/settings/tokens**

- Click "Generate new token (classic)"
- Give it a name like "nanobot-agent"
- Select scopes:
  - `repo` (full repository access — read, write, push, PRs)
  - `workflow` (trigger and manage GitHub Actions)
  - `read:org` (if working with organization repos)
- Set expiration (90 days is a good default)
- Copy the token — it won't be shown again

**Step 2: Configure git to store the token**

```bash
# Set up the credential helper to cache credentials
# "store" saves to ~/.git-credentials in plaintext (simple, persistent)
exec("git config --global credential.helper store")

# Now do a test operation that triggers auth — git will prompt for credentials
# Username: <their-github-username>
# Password: <paste the personal access token, NOT their GitHub password>
exec("git ls-remote https://github.com/<their-username>/<any-repo>.git")
```

After entering credentials once, they're saved and reused for all future operations.

**Alternative: cache helper (credentials expire from memory)**

```bash
exec("git config --global credential.helper 'cache --timeout=28800'")
```

**Alternative: set the token directly in the remote URL (per-repo)**

```bash
exec("git remote set-url origin https://<username>:<token>@github.com/<owner>/<repo>.git")
```

**Step 3: Configure git identity**

```bash
exec("git config --global user.name 'Their Name'")
exec("git config --global user.email 'their-email@example.com'")
```

**Step 4: Verify**

```bash
exec("git ls-remote https://github.com/<their-username>/<any-repo>.git")
exec("git config --global user.name")
exec("git config --global user.email")
```

### Option B: SSH Key Authentication

Good for users who prefer SSH or already have keys set up.

**Step 1: Check for existing SSH keys**

```bash
exec("ls -la ~/.ssh/id_*.pub 2>/dev/null || echo 'No SSH keys found'")
```

**Step 2: Generate a key if needed**

```bash
exec("ssh-keygen -t ed25519 -C 'their-email@example.com' -f ~/.ssh/id_ed25519 -N ''")
exec("cat ~/.ssh/id_ed25519.pub")
```

Tell the user to add the public key at: **https://github.com/settings/keys**
- Click "New SSH key"
- Paste the public key content
- Give it a title like "nanobot-agent-<machine-name>"

**Step 3: Test the connection**

```bash
exec("ssh -T git@github.com")
# Expected: "Hi <username>! You've successfully authenticated..."
```

**Step 4: Configure git to use SSH for GitHub**

```bash
exec('git config --global url."git@github.com:".insteadOf "https://github.com/"')
```

**Step 5: Configure git identity**

```bash
exec("git config --global user.name 'Their Name'")
exec("git config --global user.email 'their-email@example.com'")
```

---

## Method 2: gh CLI Authentication

If `gh` is installed, it handles both API access and git credentials in one step.

### Interactive Browser Login (Desktop)

```bash
exec("gh auth login")
```

### Token-Based Login (Headless / SSH Servers)

```bash
exec("echo '<THEIR_TOKEN>' | gh auth login --with-token")
exec("gh auth setup-git")
```

### Verify

```bash
exec("gh auth status")
```

---

## Using the GitHub API Without gh

When `gh` is not available, you can still access the full GitHub API using `curl` with a personal access token.

### Setting the Token for API Calls

```bash
# Option 1: Export as env var (preferred — keeps it out of commands)
export GITHUB_TOKEN="<token>"

# Then use in curl calls:
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user
```

### Extracting the Token from Git Credentials

If git credentials are already configured (via credential.helper store), the token can be extracted:

```bash
grep "github.com" ~/.git-credentials 2>/dev/null | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|'
```

### Helper: Detect Auth Method

Use this pattern at the start of any GitHub workflow:

```bash
# Try gh first, fall back to git + curl
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  echo "AUTH_METHOD=gh"
elif [ -n "$GITHUB_TOKEN" ]; then
  echo "AUTH_METHOD=curl"
elif grep -q "github.com" ~/.git-credentials 2>/dev/null; then
  export GITHUB_TOKEN=$(grep "github.com" ~/.git-credentials | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|')
  echo "AUTH_METHOD=curl"
else
  echo "AUTH_METHOD=none"
  echo "Need to set up authentication first"
fi
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `git push` asks for password | GitHub disabled password auth. Use a personal access token as the password, or switch to SSH |
| `remote: Permission to X denied` | Token may lack `repo` scope — regenerate with correct scopes |
| `fatal: Authentication failed` | Cached credentials may be stale — run `git credential reject` then re-authenticate |
| `ssh: connect to host github.com port 22: Connection refused` | Try SSH over HTTPS port: add `Host github.com` with `Port 443` and `Hostname ssh.github.com` to `~/.ssh/config` |
| Credentials not persisting | Check `git config --global credential.helper` — must be `store` or `cache` |
| Multiple GitHub accounts | Use SSH with different keys per host alias in `~/.ssh/config`, or per-repo credential URLs |
| `gh: command not found` + no sudo | Use git-only Method 1 above — no installation needed |
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
