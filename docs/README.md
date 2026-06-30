# NanoBot 文档

> NanoBot 是一个轻量级个人 AI 助手框架（`nanobot-ai`），支持多 LLM 提供商、多消息通道、WebUI 管理、记忆系统、技能系统和定时任务。

## 入门

| 文档 | 说明 |
|------|------|
| [快速入门](quick-start.md) | 从安装到运行的第一步 |
| [CLI 命令参考](cli-reference.md) | 所有 `nanobot` CLI 子命令的详细用法 |
| [配置参考](configuration.md) | 配置文件完整说明，含所有 Provider 和通道配置项 |
| [部署指南](deployment.md) | Docker、Systemd、macOS LaunchAgent 部署方案 |

## 核心功能

| 文档 | 说明 |
|------|------|
| [代理系统](AGENTS.md) | 代理架构概览：主循环、执行器、上下文管理、钩子系统、Coding Agent 模式 |
| [子代理网络](agent-social-network.md) | 子代理（Subagent）的生成、通信与编排机制 |
| [工具参考](tools-reference.md) | AI 可调用的所有工具完整列表与说明 |
| [记忆系统](memory.md) | 记忆提取、存储、向量索引、Git 版本管理与上下文注入 |
| [技能开发指南](skills-guide.md) | 技能系统架构，编写和注册自定义技能 |
| [定时任务](cron.md) | Cron 定时任务配置和管理 |
| [My 工具](my-tool.md) | 用户个人信息管理工具 |

## 通道与 API

| 文档 | 说明 |
|------|------|
| [聊天应用](chat-apps.md) | 聊天命令系统架构 |
| [聊天命令参考](chat-commands.md) | 所有聊天命令列表 |
| [通道插件开发](channel-plugin-guide.md) | 开发自定义消息通道插件 |
| [HTTP API](openai-api.md) | 管理 API 端点说明 |
| [WebSocket 协议](websocket.md) | WebSocket 连接与 Hub TCP 协议 |
| [Python SDK](python-sdk.md) | 通过 Python 调用 API 的示例 |

## Web 界面

| 文档 | 说明 |
|------|------|
| [WebUI](webui.md) | 浏览器管理界面：设置、配置、记忆搜索与聊天 |

## 高级主题

| 文档 | 说明 |
|------|------|
| [提示词模板](prompt-templates.md) | SOUL/USER 模板系统，自定义 AI 行为 |
| [多实例部署](multiple-instances.md) | 运行多个 NanoBot 实例共享 Hub |
| [安全配置](security.md) | SSRF 保护、沙箱、Shell 安全等 |
| [MCP 协议](mcp.md) | Model Context Protocol 集成配置 |
| [语音转文字](transcription.md) | 语音消息转写配置 |

## 运维

| 文档 | 说明 |
|------|------|
| [故障排查](troubleshooting.md) | 常见问题诊断与解决 |
