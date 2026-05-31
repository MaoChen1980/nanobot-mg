---
name: tmux
description: Runs interactive CLI programs in persistent terminal sessions. Supports SSH, password prompts, REPLs, and any tool needing back-and-forth input and output (shell, cmd, etc.). Use when the user needs to SSH, enter passwords, run REPLs, or interact with a CLI program.
version: 0.1.0
---

# tmux Skill, tools from system

使用 tmux 处理任何 CLI 任务——它提供真正的终端，正确处理信号，并可靠地捕获输出。

## Install

- **Linux**: `apt install tmux` or `brew install tmux`
- **macOS**: `brew install tmux`
- **Windows**: `winget install psmux`（然后重启 shell）

## Quickstart (isolated socket, exec tool)

```bash
SOCKET_DIR="${NANOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/nanobot-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/nanobot.sock"
SESSION=nanobot-python

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'PYTHON_BASIC_REPL=1 python3 -q' Enter
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

启动 session 后，始终打印监控命令：

```
To monitor:
  tmux -S "$SOCKET" attach -t "$SESSION"
  tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

## Socket convention

- 使用 `NANOBOT_TMUX_SOCKET_DIR` 环境变量。
- 默认 socket 路径：`"$NANOBOT_TMUX_SOCKET_DIR/nanobot.sock"`。

## Targeting panes and naming

- 目标格式：`session:window.pane`（默认为 `:0.0`）。
- 名称保持简短，避免空格。
- 检查：`tmux -S "$SOCKET" list-sessions`，`tmux -S "$SOCKET" list-panes -a`。

## Finding sessions

- 列出当前 socket 上的 sessions：`{baseDir}/scripts/find-sessions.sh -S "$SOCKET"`。
- 扫描所有 sockets：`{baseDir}/scripts/find-sessions.sh --all`（使用 `NANOBOT_TMUX_SOCKET_DIR`）。

## Sending input safely

- 优先使用字面发送：`tmux -S "$SOCKET" send-keys -t target -l -- "$cmd"`。
- 控制键：`tmux -S "$SOCKET" send-keys -t target C-c`。
- **卡死进程**：如果进程挂起（例如 SSH 重试错误密码），发送 Ctrl-C 中断：`tmux -S "$SOCKET" send-keys -t target C-c`。与 `exec` 的直接信号传递不同，tmux 进程是隔离的——必须作为按键事件发送到 pane。

## Watching output

- 捕获最近历史：`tmux -S "$SOCKET" capture-pane -p -J -t target -S -200`。
- 等待提示符：`{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern'`。
- 可以附加（attach）；用 `Ctrl+b d` 分离（detach）。

## Spawning processes

- 对于 python REPL，设置 `PYTHON_BASIC_REPL=1`（非 basic REPL 会破坏 send-keys 流程）。

## Windows

- 安装 **psmux**（`winget install psmux`）——一个 PowerShell tmux 克隆。命令完全相同（`send-keys`、`capture-pane` 等）。
- psmux 将 `tmux` 注册为命令别名。使用方式与 Linux tmux 相同。
- 安装后，重启 shell 以更新 PATH。

## Orchestrating Coding Agents (Codex, Claude Code)

tmux 擅长并行运行多个编码 agent：

```bash
SOCKET="${TMPDIR:-/tmp}/codex-army.sock"

# 创建多个 sessions
for i in 1 2 3 4 5; do
  tmux -S "$SOCKET" new-session -d -s "agent-$i"
done

# 在不同工作目录启动 agents
tmux -S "$SOCKET" send-keys -t agent-1 "cd /tmp/project1 && codex --yolo 'Fix bug X'" Enter
tmux -S "$SOCKET" send-keys -t agent-2 "cd /tmp/project2 && codex --yolo 'Fix bug Y'" Enter

# 轮询完成状态（检查提示符是否返回）
for sess in agent-1 agent-2; do
  if tmux -S "$SOCKET" capture-pane -p -t "$sess" -S -3 | grep -q "❯"; then
    echo "$sess: DONE"
  else
    echo "$sess: Running..."
  fi
done

# 获取已完成 session 的完整输出
tmux -S "$SOCKET" capture-pane -p -t agent-1 -S -500
```

**提示：**
- 为并行修复使用独立的 git worktree（避免分支冲突）
- 在新克隆中运行 codex 前先执行 `pnpm install`
- 检查 shell 提示符（`❯` 或 `$`）来判断是否完成
- Codex 需要 `--yolo` 或 `--full-auto` 用于非交互式修复

## Cleanup

- 关闭 session：`tmux -S "$SOCKET" kill-session -t "$SESSION"`。
- 关闭 socket 上所有 sessions：`tmux -S "$SOCKET" list-sessions -F '#{session_name}' | xargs -r -n1 tmux -S "$SOCKET" kill-session -t`。
- 移除私有 socket 上所有内容：`tmux -S "$SOCKET" kill-server`。

## Helper: wait-for-text.sh

`{baseDir}/scripts/wait-for-text.sh` 轮询 pane，通过正则（或固定字符串）匹配，支持超时。

```bash
{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern' [-F] [-T 20] [-i 0.5] [-l 2000]
```

- `-t`/`--target` pane 目标（必填）
- `-p`/`--pattern` 要匹配的正则（必填）；添加 `-F` 表示固定字符串
- `-T` 超时秒数（整数，默认 15）
- `-i` 轮询间隔秒数（默认 0.5）
- `-l` 搜索的历史行数（整数，默认 1000）

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
