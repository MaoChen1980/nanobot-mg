# NanoBot CLI 参考文档

## 概述

NanoBot CLI 基于 [Typer](https://typer.tiangolo.com/) 构建，入口为 `nanobot` 命令。CLI 负责配置初始化、Gateway 启动、Agent 交互、频道管理和提供商认证等操作。

```bash
nanobot [OPTIONS] COMMAND [ARGS]...
```

---

## 全局选项

| 选项 | 说明 |
|------|------|
| `-v`, `--version` | 显示 NanoBot 版本号并退出 |
| `-h`, `--help` | 显示帮助信息 |

---

## 命令列表

| 命令 | 类型 | 说明 |
|------|------|------|
| [`init`](#init) | 一级命令 | 扫描项目目录，生成 `project_card.md` 供 Coding Agent 使用 |
| [`onboard`](#onboard) | 子命令组 | 初始化 NanoBot 配置，创建频道机器人 |
| [`gateway`](#gateway) | 一级命令 | 启动 NanoBot Gateway（Web 管理界面） |
| [`agent`](#agent) | 一级命令 | 与 Agent 交互（单次消息或交互式对话） |
| [`status`](#status) | 一级命令 | 显示 NanoBot 运行状态 |
| [`provider`](#provider) | 子命令组 | 管理 LLM 提供商（OAuth 登录） |
| [`channels`](#channels) | 子命令组 | 管理频道配置与状态 |
| [`plugins`](#plugins) | 子命令组 | 查看已发现的频道插件 |

> **注意**：`serve` 命令已不存在，功能已由 `gateway` 命令替代。`chat` 命令亦不存在，交互式对话功能集成在 `agent` 命令中。

---

## init

扫描项目目录，生成 `project_card.md` 文件，供 Coding Agent 理解项目结构和上下文。

```bash
nanobot init [OPTIONS] [PROJECT_DIR]
```

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `PROJECT_DIR` | 位置参数 | `.` | 要扫描的项目目录路径 |

**选项**:

| 选项 | 说明 |
|------|------|
| `-c`, `--config PATH` | 指定 NanoBot 配置文件路径 |
| `-f`, `--force` | 强制重新扫描（即使 `project_card.md` 已存在） |

**功能说明**:
- 读取目标目录的实际文件系统内容，生成结构化的项目卡片
- 如果 `tasks/` 目录不存在则自动创建
- 如果 `tasks/tree.json` 不存在则创建初始任务树文件

---

## onboard

初始化 NanoBot 配置和 workspace，以及创建各类聊天频道的机器人。

```bash
nanobot onboard [OPTIONS]
```

**选项**:

| 选项 | 说明 |
|------|------|
| `-w`, `--workspace TEXT` | 指定 workspace 目录 |
| `-c`, `--config PATH` | 指定配置文件路径 |

**功能说明**:
- 如果配置文件不存在，生成默认 `config.json`
- 如果 workspace 目录不存在则自动创建
- 同步 workspace 模板文件
- 显示后续操作指引（启动 gateway）

### onboard feishu

通过二维码扫描创建和配置飞书机器人。

```bash
nanobot onboard feishu [OPTIONS]
```

**选项**:

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `-n`, `--name TEXT` | `feishu-bot` | 配置中的机器人名称 |
| `-c`, `--config PATH` | — | 指定配置文件路径 |

### onboard dingtalk

通过二维码扫描创建和配置钉钉机器人。

```bash
nanobot onboard dingtalk [OPTIONS]
```

**选项**:

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `-n`, `--name TEXT` | `dingtalk-bot` | 配置中的机器人名称 |
| `-c`, `--config PATH` | — | 指定配置文件路径 |

---

## gateway

启动 NanoBot Gateway，提供 Web 管理界面。

```bash
nanobot gateway [OPTIONS]
```

**选项**:

| 选项 | 说明 |
|------|------|
| `-p`, `--port INT` | 指定 Gateway 端口号 |
| `-w`, `--workspace TEXT` | 指定 workspace 目录 |
| `-c`, `--config PATH` | 指定配置文件路径 |
| `-v`, `--verbose` | 启用详细日志输出（设置日志级别为 DEBUG） |

**功能说明**:
- 启动 GatewayApplication，提供 WebUI 界面
- 如果配置的 host 为 `0.0.0.0` 或 `::`，则浏览器访问时使用 `127.0.0.1`
- Gateway 启动后会自动打开浏览器

---

## agent

与 NanoBot Agent 直接交互，支持单次消息模式和交互式对话模式。

```bash
nanobot agent [OPTIONS]
```

**选项**:

| 选项 | 说明 |
|------|------|
| `-m`, `--message TEXT` | 要发送给 Agent 的消息（若提供则执行单次对话，否则进入交互模式） |
| `-s`, `--session TEXT` | 会话 ID（默认：`cli:direct`） |
| `-w`, `--workspace TEXT` | 指定 workspace 目录 |
| `-c`, `--config PATH` | 指定配置文件路径 |
| `-p`, `--project-root PATH` | 项目根目录（启用 Coding Agent 模式，配合项目卡片使用） |
| `--markdown / --no-markdown` | 是否以 Markdown 格式渲染 Assistant 输出（默认：启用） |
| `--logs / --no-logs` | 是否显示 NanoBot 运行时日志（默认：禁用） |
| `-d`, `--debug` | 启用调试模式（将原始 Prompt 保存到 `~/.nanobot/debug/`） |

**功能说明**:

- **单次消息模式**（`--message`）：发送一条消息给 Agent，等待响应后退出。使用 StreamRenderer 进行流式渲染。
- **交互模式**（不带 `--message`）：进入交互式对话会话。
  - 使用 `prompt_toolkit` 提供命令历史、粘贴支持、自动补全
  - 输入 `exit`、`quit` 或按 `Ctrl+C` 退出
  - 支持 Cron 定时任务触发
  - 支持工具调用进度提示（`send_tool_hints`）
  - 流式输出渲染

---

## status

显示 NanoBot 当前状态。

```bash
nanobot status
```

**功能说明**:
- 显示配置文件路径及是否存在
- 显示 workspace 路径及是否存在
- 显示当前使用的模型名称
- 逐项检查各 LLM 提供商的 API key 或 API base 配置状态（OAuth 提供商标注为 ✓）

---

## provider

管理 LLM 提供商，主要提供 OAuth 认证功能。

```bash
nanobot provider COMMAND [ARGS]
```

### provider login

对指定的 OAuth 提供商进行登录认证。

```bash
nanobot provider login [OPTIONS] PROVIDER
```

**参数**:

| 参数 | 说明 |
|------|------|
| `PROVIDER` | 提供商名称（例如 `openai-codex`、`github-copilot`） |

**支持的提供商**:

| 名称 | 认证方式 |
|------|----------|
| `openai-codex` | 交互式 OAuth 登录（`oauth-cli-kit`） |
| `github-copilot` | GitHub Device Flow |

**功能说明**:
- 支持基于 OAuth 的提供商认证
- 自动检查是否已有有效的 Token
- 如未登录则启动交互式认证流程

---

## channels

管理 NanoBot 的聊天频道。

```bash
nanobot channels COMMAND [ARGS]
```

### channels status

显示各频道的启用状态。

```bash
nanobot channels status [OPTIONS]
```

**选项**:

| 选项 | 说明 |
|------|------|
| `-c`, `--config PATH` | 指定配置文件路径 |

**功能说明**:
- 列出所有已发现的频道
- 显示每个频道的启用/禁用状态（✓ / ✗）

### channels login

对指定频道进行登录认证。

```bash
nanobot channels login [OPTIONS] CHANNEL_NAME
```

**参数**:

| 参数 | 说明 |
|------|------|
| `CHANNEL_NAME` | 频道名称（例如 `weixin`、`whatsapp`） |

**选项**:

| 选项 | 说明 |
|------|------|
| `-f`, `--force` | 强制重新认证（即使已登录） |
| `-c`, `--config PATH` | 指定配置文件路径 |

**功能说明**:
- 验证频道名称是否有效
- 代理类频道通过配置文件进行认证（需手动编辑 `config.json`）

---

## plugins

管理频道插件。

```bash
nanobot plugins COMMAND [ARGS]
```

### plugins list

列出所有已发现的频道插件。

```bash
nanobot plugins list
```

**功能说明**:
- 显示所有内置频道和通过插件机制发现的频道
- 每个频道显示：名称、来源（builtin / plugin）、启用状态（yes / no）

---

## 退出命令（交互模式下）

在 `agent` 交互模式下，以下命令均可退出：

| 命令 | 说明 |
|------|------|
| `exit` | 退出交互会话 |
| `quit` | 退出交互会话 |
| `/exit` | 退出交互会话 |
| `/quit` | 退出交互会话 |
| `:q` | 退出交互会话 |
| `Ctrl+C` | 中断并退出 |
| `Ctrl+D` (EOF) | 中断并退出 |
