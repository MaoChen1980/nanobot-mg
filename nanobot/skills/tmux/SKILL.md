---
name: tmux
description: '管理交互式终端会话，支持 SSH、REPL、密码输入等需要持久终端的场景。

  当用户需要 SSH 连接、运行交互式程序、输入密码、启动 REPL、或要求"在后台运行"时激活。'
version: 0.1.0
category: domain-specific
---

# tmux Skill

Use tmux for any CLI task that requires a real terminal — it provides a true PTY, correctly handles signals, and reliably captures output.

## When to Use

- SSH into a remote server or enter passwords
- Run REPLs (Python, Node, etc.) that need back-and-forth input and output
- Interact with any CLI program that requires a persistent session
- Run multiple coding agents (Codex, Claude Code) in parallel
- Execute commands that need isolation from the main agent process

## Steps

1. **Install tmux**:
   - Linux: `apt install tmux` or `brew install tmux`
   - macOS: `brew install tmux`
   - Windows: `winget install psmux` (then restart shell; psmux registers `tmux` as a command alias — usage is identical to Linux tmux)

2. **Create an isolated socket** — use a dedicated socket directory to avoid collisions:
   ```bash
   SOCKET_DIR="${NANOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/nanobot-tmux-sockets}"
   mkdir -p "$SOCKET_DIR"
   SOCKET="$SOCKET_DIR/nanobot.sock"
   ```

3. **Start a session** — create a new detached session:
   ```bash
   SESSION=nanobot-python
   tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
   ```

4. **Send input to a pane** — use literal send mode (`-l`) for reliable text input:
   ```bash
   tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -l -- "command here" Enter
   ```
   Target format: `session:window.pane` (default pane is `:0.0`).
   For Python REPLs, always set `PYTHON_BASIC_REPL=1` before launching (non-basic REPL breaks send-keys flow):
   ```bash
   tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'PYTHON_BASIC_REPL=1 python3 -q' Enter
   ```

5. **Capture output** — retrieve recent pane history:
   ```bash
   tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
   ```
   Print the monitoring command after starting a session:
   ```
   To monitor:
     tmux -S "$SOCKET" attach -t "$SESSION"
     tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
   ```

6. **Wait for specific output** using the helper script at `{baseDir}/scripts/wait-for-text.sh` — polls a pane until a pattern matches (or timeout):
   ```bash
   {baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern' [-F] [-T 20] [-i 0.5] [-l 2000]
   ```
   - `-t`/`--target`: pane target (required)
   - `-p`/`--pattern`: regex or fixed string (add `-F` for fixed string)
   - `-T`: timeout in seconds (integer, default 15)
   - `-i`: poll interval (default 0.5)
   - `-l`: history lines to search (integer, default 1000)

7. **Orchestrate parallel coding agents** — run multiple Codex or Claude Code instances:
   ```bash
   SOCKET="${TMPDIR:-/tmp}/codex-army.sock"

   for i in 1 2 3 4 5; do
     tmux -S "$SOCKET" new-session -d -s "agent-$i"
   done

   tmux -S "$SOCKET" send-keys -t agent-1 "cd /tmp/project1 && codex --yolo 'Fix bug X'" Enter
   tmux -S "$SOCKET" send-keys -t agent-2 "cd /tmp/project2 && codex --yolo 'Fix bug Y'" Enter
   ```
   Poll for completion by checking the shell prompt:
   ```bash
   for sess in agent-1 agent-2; do
     if tmux -S "$SOCKET" capture-pane -p -t "$sess" -S -3 | grep -q "❯"; then
       echo "$sess: DONE"
     else
       echo "$sess: Running..."
     fi
   done
   ```
   Tips for agent orchestration:
   - Use separate git worktrees to avoid branch conflicts
   - Run `pnpm install` before launching codex in fresh clones
   - Codex requires `--yolo` or `--full-auto` for non-interactive fixes

8. **Clean up** sessions and sockets when finished:
   - Kill a single session: `tmux -S "$SOCKET" kill-session -t "$SESSION"`
   - Kill all sessions on a socket: `tmux -S "$SOCKET" list-sessions -F '#{session_name}' | xargs -r -n1 tmux -S "$SOCKET" kill-session -t`
   - Kill the server (removes everything on the socket): `tmux -S "$SOCKET" kill-server`

9. **验证**: 对照 Verification 章节逐条检查。全部通过则完成；不通过则加载 skill-manager 修复此 skill。

## Verification

- Run `tmux -S "$SOCKET" list-sessions` to confirm the session was created
- Run `tmux -S "$SOCKET" list-panes -a` to inspect all panes and their status
- Run `tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200` and verify output contains expected text
- For agent orchestration, confirm each agent session is running with `tmux -S "$SOCKET" list-sessions`
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Pitfalls

- **psmux on Windows**: `winget install psmux` then restart shell. After installation, `tmux` commands work identically to Linux. If commands fail, ensure the shell PATH was reloaded
- **Ctrl-C for stuck processes**: Unlike direct signal delivery via `exec`, tmux processes are isolated. To interrupt a stuck process (e.g., SSH retrying with wrong password), send Ctrl-C as a key event to the pane: `tmux -S "$SOCKET" send-keys -t target C-c`
- **PYTHON_BASIC_REPL=1**: Always set this environment variable when starting a Python REPL in tmux. Non-basic REPLs use readline which intercepts send-keys and breaks the input flow
- **Socket path convention**: Default socket is `"$SOCKET_DIR/nanobot.sock"` using `NANOBOT_TMUX_SOCKET_DIR`. Override via `NANOBOT_TMUX_SOCKET_DIR` environment variable. Use `find-sessions.sh` scripts to discover sessions — run `{baseDir}/scripts/find-sessions.sh -S "$SOCKET"` for current socket or `{baseDir}/scripts/find-sessions.sh --all` to scan all sockets
- **Session naming**: Keep names short and avoid spaces. Check existing sessions with `tmux -S "$SOCKET" list-sessions`
- **Detaching from attach**: When monitoring via `attach`, press `Ctrl+b d` to detach without killing the session
