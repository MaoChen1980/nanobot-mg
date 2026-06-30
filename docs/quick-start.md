# 快速入门

本节带你从零开始完成 NanoBot 的安装、初始化配置、启动服务，并与 AI 对话。

---

## 1. 安装

```bash
pip install nanobot-ai
```

要求 Python >= 3.10。

验证安装：

```bash
nanobot --version
```

---

## 2. 初始化配置

执行以下命令生成默认配置文件和工作目录：

```bash
nanobot onboard
```

这会在 `~/.nanobot/config.json` 创建默认配置，并创建默认工作区 `~/.nanobot/workspace`。

如需指定自定义路径：

```bash
nanobot onboard --config /path/to/config.json --workspace /path/to/workspace
```

### 手动配置

编辑 `~/.nanobot/config.json`，添加 LLM 提供商 API 密钥。例如配置 OpenAI：

```json
{
  "providers": {
    "openai": {
      "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "gpt-4o"
    }
  }
}
```

查看当前状态：

```bash
nanobot status
```

---

## 3. 启动 Gateway（WebUI + 通道服务）

```bash
nanobot gateway
```

Gateway 会启动 WebUI 管理页面（默认 `http://127.0.0.1:8080`）和消息通道服务。

常用选项：

- `--port` / `-p`：指定端口（默认 8080）
- `--config` / `-c`：指定配置文件
- `--workspace` / `-w`：指定工作区
- `--verbose` / `-v`：显示详细日志

---

## 4. 使用 CLI 聊天

### 交互模式

直接运行，进入交互式对话：

```bash
nanobot agent
```

支持上下翻页浏览历史，输入 `exit`、`quit` 或 `Ctrl+C` 退出。

### 单条消息模式

一次性发送消息并获取回复：

```bash
nanobot agent --message "你好，请介绍一下你自己"
```

### 编程助手模式

为指定项目目录生成项目卡片，然后启动编程助手：

```bash
nanobot init /path/to/project
nanobot agent --project-root /path/to/project
```

### 其他常用选项

- `--model / --no-model`：Markdown 渲染开关（默认开启）
- `--logs / --no-logs`：运行时日志开关（默认关闭）
- `--debug / -d`：调试模式，保存原始提示词到 `~/.nanobot/debug/`
- `--session / -s`：指定会话 ID（默认 `cli:direct`）

---

## 5. 设置消息通道

### 查看支持的通道

```bash
nanobot plugins list
```

查看各通道启用状态：

```bash
nanobot channels status
```

### 飞书（Feishu）自动配置

通过设备 OAuth 流程扫码注册并自动写入配置：

```bash
nanobot onboard feishu
```

此命令会：
1. 连接飞书开放平台开始注册流程
2. 在终端显示二维码
3. 用飞书 App 扫码确认授权
4. 自动创建机器人并启用 Bot 能力
5. 将凭据写入 `config.json`

完成后启动 gateway，再到飞书开发者后台配置权限和事件订阅即可使用。

### 钉钉（DingTalk）自动配置

```bash
nanobot onboard dingtalk
```

流程同飞书，通过钉钉 App 扫码完成机器人注册和配置。

### 其他通道

对于 Telegram、Discord、Slack、QQ、微信、Email、WhatsApp 等通道，通过编辑 `config.json` 手动配置凭据：

```bash
nanobot channels login <channel_name>
```

部分通道（如微信、WhatsApp）属于代理通道，需要额外的登录步骤。

---

## 6. OAuth 提供商登录

对于需要 OAuth 认证的 LLM 提供商（如 OpenAI Codex、GitHub Copilot）：

```bash
nanobot provider login openai-codex
nanobot provider login github-copilot
```

---

## 7. 启动定时任务

NanoBot 的定时任务在 `nanobot agent` 交互模式下自动运行。你可以通过对话让 AI 帮你创建和管理定时提醒。

---

## 下一步

- 阅读配置指南了解完整配置项
- 阅读通道集成文档配置你的消息平台
- 阅读技能开发文档编写自定义技能
- 阅读 API 参考了解 OpenAI 兼容接口
