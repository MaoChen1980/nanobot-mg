# WebUI 文档

NanoBot 的 WebUI 是一个浏览器端管理界面，内嵌于 gateway 进程中，提供配置管理、记忆搜索与问答、工作区文件浏览等功能。

---

## 目录

- [WebUI 概述](#webui-概述)
- [启动方式](#启动方式)
- [访问地址](#访问地址)
- [主要功能页面](#主要功能页面)
  - [Settings 标签页](#settings-标签页)
  - [Agent 子标签](#agent-子标签)
  - [Channels 子标签](#channels-子标签)
  - [Providers 子标签](#providers-子标签)
  - [Tools 子标签](#tools-子标签)
  - [Gateway 子标签](#gateway-子标签)
  - [Memory 标签页](#memory-标签页)
- [后端 API 端点](#后端-api-端点)
  - [GET /health](#get-health)
  - [GET /api/config](#get-apiconfig)
  - [PUT /api/config](#put-apiconfig)
  - [GET /api/settings](#get-apisettings)
  - [PUT /api/settings/update](#put-apisettingsupdate)
  - [GET /api/provider-models](#get-apiprovider-models)
  - [GET /api/workspace/file](#get-apiworkspacefile)
  - [GET /api/memory/search](#get-apimemorysearch)
  - [POST /api/memory/rebuild-index](#post-apimemoryrebuild-index)
  - [POST /api/memory/chat](#post-apimemorychat)
  - [POST /api/shutdown](#post-apishutdown)
  - [POST /api/stop](#post-apistop)
- [WebUI 开发指南](#webui-开发指南)
- [常见问题](#常见问题)

---

## WebUI 概述

WebUI 提供以下功能范围：

- **配置管理**：通过浏览器界面修改 NanoBot 的所有配置项（AI 模型、消息通道、工具开关、Gateway 参数等），支持保存与热重载
- **记忆浏览与搜索**：查看工作区 memory 目录下的 Markdown 文件，支持 FAISS 向量搜索 + grep 回退的全文检索
- **AI 记忆聊天**：基于检索到的记忆上下文，与 LLM 进行对话（SSE 流式输出）
- **工作区文件读取**：通过路径参数读取工作区内的任意文件（受路径穿越防护限制）
- **Gateway 生命周期管理**：通过 WebUI 执行重启与停止操作

WebUI 为单页面应用（SPA），使用原生 HTML / CSS / JavaScript 实现，运行时通过 marked.js 渲染 Markdown，通过 mermaid.js 渲染图表。

---

## 启动方式

WebUI 内嵌于 gateway 进程中，启动 gateway 即可同时启动 WebUI：

```bash
nanobot gateway
```

Gateway 启动时会自动定位 `nanobot/web/index.html`（依次检查源代码目录和当前工作目录），并将其注册为根路径的静态首页。

可用选项：

| 选项 | 说明 |
|------|------|
| `--port` / `-p` | 指定端口（默认读取配置中的 `gateway.port`） |
| `--config` / `-c` | 指定配置文件路径 |
| `--workspace` / `-w` | 指定工作区目录 |
| `--verbose` / `-v` | 显示详细日志 |

Gateway 启动后，控制台会打印 WebUI 地址：

```
Starting nanobot gateway version x.y.z on port 18790...
✓ WebUI at http://127.0.0.1:18790
```

---

## 访问地址

默认地址：`http://localhost:18790`

如果 gateway 绑定到 `0.0.0.0` 或 `::`，显示地址自动转换为 `127.0.0.1`。若配置了自定义 host，则使用配置的 host。

端口可在配置文件 `gateway.port` 中修改，或通过 `--port` 命令行参数指定。

---

## 主要功能页面

### Settings 标签页

Settings 是 WebUI 的主标签页，包含五个子标签（通过标签栏切换）。所有配置修改在底部 footer 点击 "Apply" 后生效。

#### 配置编辑机制

- 前端加载完整配置 JSON，解析为表单字段
- 每个字段通过 `data-path` 属性关联配置路径（如 `agents.defaults.model`）
- 保存时只提交变更的字段路径，后端通过 `model_validate` 校验完整配置
- 部分配置项支持热重载（无需重启），其余配置变更会弹出重启确认对话框

**支持热重载的配置路径：**

```
agents.defaults.assess_interval
agents.defaults.history_token_limit
agents.defaults.compress_trigger_tokens
agents.defaults.context_window_tokens
agents.defaults.context_block_limit
agents.defaults.max_tool_result_chars
agents.defaults.max_tool_iterations
agents.defaults.provider_retry_mode
agents.defaults.model
agents.defaults.provider
agents.defaults.max_tokens
agents.defaults.temperature
agents.defaults.reasoning_effort
```

---

### Agent 子标签

AI Model 配置卡片：

| 字段 | 类型 | 说明 |
|------|------|------|
| Provider | 下拉选择 | 20+ 模型提供商（OpenAI、Anthropic、DeepSeek、Gemini、Ollama 等） |
| Model | 下拉选择 | 选中 Provider 后自动从 API 拉取可用模型列表 |
| Workspace | 文本 | 工作区路径 |
| Max Tokens | 数字 | 最大生成 token 数 |
| Temperature | 数字 | 生成温度（0-2） |
| Max Tool Iterations | 数字 | 单次对话最大工具调用次数 |
| Max Tool Result Chars | 数字 | 工具返回结果最大字符数 |
| Context Window (tokens) | 数字 | 上下文窗口大小 |
| Context Block Limit | 文本 | 上下文块数上限（可为空） |
| Provider Retry | 下拉 | Provider 错误重试策略（Standard / Persistent） |
| Reasoning Effort | 下拉 | 推理努力程度（low / medium / high / max / adaptive） |

Session 配置卡片：

| 字段 | 类型 | 说明 |
|------|------|------|
| Timezone | 下拉选择 | 时区（默认 Asia/Shanghai） |
| Compress Trigger (tokens) | 数字 | 触发上下文压缩的 token 阈值 |
| History Token Limit | 数字 | 历史消息 token 上限 |
| Disabled Skills | 文本 | 禁用的技能名称，逗号分隔 |

Extractor 配置卡片：

| 字段 | 类型 | 说明 |
|------|------|------|
| Interval (h) | 数字 | 记忆提取器运行间隔（小时） |
| Save Interval (turns) | 数字 | 每 N 轮对话保存一次 |

Assessment 配置卡片：

| 字段 | 类型 | 说明 |
|------|------|------|
| Assess Interval | 数字 | 每 N 次 LLM 请求触发一次自我评估 |

---

### Channels 子标签

消息通道配置页面。支持以下通道类型（各通道显示为独立卡片）：

- dingtalk（钉钉）
- discord（Discord）
- email（邮件）
- feishu（飞书）
- qq（QQ）
- slack（Slack）
- telegram（Telegram）
- weixin（微信）
- whatsapp（WhatsApp）
- websocket（WebSocket）

每个通道卡片包含：

- **启用开关**：右侧 toggle 开关，控制通道启用状态
- **通道字段**：根据通道类型动态生成（如 token、app_id、app_secret 等）
- **敏感字段**：密码类字段使用 `type="password"` 隐藏值（如 token、app_secret、api_key 等）
- **Bot 管理**：飞书、钉钉、QQ 等通道支持多 Bot 配置，每个 Bot 显示为独立的子卡片，可新增或删除

操作方式：

- 点击卡片标题区域展开/收起详情
- 未添加的通道显示为半透明占位卡片，点击 "+" 按钮添加
- 修改后点击底部 "Apply" 保存

---

### Providers 子标签

以网格布局（两列）展示所有支持的模型提供商的 API 密钥配置：

| 字段 | 类型 | 说明 |
|------|------|------|
| API Key | 文本 | 提供商 API 密钥 |
| API Base | 文本 | 自定义 API 基础 URL（可选） |

每个已配置密钥的提供商卡片右上角显示 "KEY" 徽章。

支持的提供商列表：openai、anthropic、deepseek、groq、minimax（含变体）、moonshot、gemini、ollama、azure_openai、openrouter、dashscope、zhipu、mistral、stepfun、qianfan、custom、vllm、lm_studio、ovms、siliconflow、volcengine、wecom。

---

### Tools 子标签

工具开关与配置：

**Web 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Enabled | 开关 | 启用网页内容抓取工具 |
| Proxy | 文本 | HTTP 代理地址 |
| User Agent | 文本 | 自定义 User-Agent |

**Web Search 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Provider | 下拉 | 搜索引擎（DuckDuckGo / Google / Bing / SearXNG / Jina / Kagi / Tavily） |
| API Key | 密码 | 搜索 API 密钥 |
| Base URL | 文本 | 自定义搜索 API 地址 |
| Max Results | 数字 | 最大返回结果数 |
| Timeout (s) | 数字 | 搜索超时时间 |

**Web Fetch 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Use Jina Reader | 开关 | 使用 Jina Reader 格式抓取网页 |

**Exec 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Enabled | 开关 | 启用命令执行工具 |
| Timeout (s) | 数字 | 命令执行超时 |
| Path Append | 文本 | 附加到 PATH 环境变量 |
| Sandbox | 文本 | 沙箱命令路径 |
| Allowed Env Keys | 文本 | 允许传递给子进程的环境变量名（逗号分隔） |

**My Tool 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Enabled | 开关 | 启用 My Tool（便签工具） |
| Allow Set | 开关 | 允许设置便签内容 |

**MCP Servers 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Servers (JSON) | 文本域 | MCP 服务器配置，JSON 格式编辑 |

**SSRF / Security 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Restrict to Workspace | 开关 | 限制工具操作仅限于工作区 |
| SSRF Whitelist | 文本 | 允许的网络 CIDR 列表（逗号分隔） |

---

### Gateway 子标签

**Gateway 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Host | 文本 | 监听地址 |
| Port | 数字 | 监听端口 |

**Heartbeat 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Enabled | 开关 | 启用心跳检测 |
| Interval (s) | 数字 | 心跳间隔（秒） |
| Keep Recent Messages | 数字 | 心跳时保留的最近消息数 |

**Logging 卡片：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Enabled | 开关 | 启用日志记录 |
| Level | 下拉 | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| File | 文本 | 日志文件路径 |
| Console | 开关 | 是否输出到控制台 |

---

### Memory 标签页

Memory 标签页提供两种模式：

#### Browse 模式（默认）

**功能：**

- 加载并渲染工作区 `memory/MEMORY.md` 文件
- 支持通过内部 Markdown 链接导航（`*.md` 链接点击后加载目标文件）
- 支持 Mermaid 图表渲染
- 导航历史栈管理，支持 "Back" 返回上级页面

**搜索功能：**

- 输入关键词后点击搜索按钮或按 Enter 触发
- 调用 `/api/memory/search` 接口
- 优先使用 FAISS 向量检索，回退到 grep 关键词匹配
- 搜索结果显示来源文件、相关度分数、匹配文本片段
- 点击搜索结果中的 "Open file" 可跳转到对应文件
- 若 LLM 解释功能可用（请求参数 `llm=1`），还会显示 LLM 对搜索结果的解读

**索引重建：**

- 点击 "Rebuild" 按钮触发 `/api/memory/rebuild-index`
- 重建 FAISS 向量索引、任务索引和技能索引
- 状态栏显示重建结果（chunks 数量、FAISS 是否可用）

#### AI Chat 模式

基于记忆上下文的 AI 对话功能：

- 点击 "AI Chat" 按钮切换进入对话模式
- 用户输入问题后，系统自动检索相关记忆作为上下文
- 通过 `/api/memory/chat` 接口（SSE 流式）获取 LLM 回复
- 回复内容使用 marked.js 渲染 Markdown
- 回复附带来源引用（source chips），点击可跳转到 Browse 模式查看源文件
- 支持对话历史追踪

---

## 后端 API 端点

所有 API 端点由 [nanobot/api/server.py](file:///e:/claude/nanobot-mg/nanobot/api/server.py) 实现，使用 Starlette 框架。API 与 WebUI 同端口提供。

### GET /

首页，返回 `index.html`。

### GET /health

健康检查。

**响应示例：**

```json
{"status": "ok"}
```

---

### GET /api/config

返回完整配置的 JSON。

**响应：** 完整的 Config 对象（`model_dump()` 输出）。

---

### PUT /api/config

保存完整配置。

**请求体：** 完整的 Config JSON。

**处理流程：**

1. 通过 `Config.model_validate()` 验证配置
2. 调用 `save_config()` 写入磁盘
3. 若修改涉及已禁用的通道，自动停止对应 proxy 进程
4. 调用 `gateway.reload_config()` 将可热重载的配置项同步到运行中服务

**响应：**

```json
{"ok": true}
```

---

### GET /api/settings

返回精简版配置状态（前端初始化用）。

**响应示例：**

```json
{
  "agent": {
    "model": "gpt-4o",
    "provider": "openai",
    "resolved_provider": "openai",
    "has_api_key": true
  },
  "providers": [
    {"name": "openai", "label": "OpenAI"}
  ],
  "runtime": {"config_path": ""},
  "requires_restart": false
}
```

---

### PUT /api/settings/update

更新部分设置项（model 和 provider）。

**请求体示例：**

```json
{"model": "gpt-4o", "provider": "openai"}
```

---

### GET /api/provider-models

从指定 Provider 的 API 拉取可用模型列表。

**参数：** `provider`（必需）- 提供商名称，如 `openai`、`anthropic`

**响应示例：**

```json
{"models": ["gpt-4o", "gpt-4o-mini", ...]}
```

**实现说明：** 根据提供商类型拼接 API URL，通过 HTTP GET 请求 `/v1/models` 端点获取，使用配置中的 `api_key` 认证。内置了 20+ 提供商的默认 API Base URL 映射。

---

### GET /api/workspace/file

读取工作区内的文件。

**参数：** `path` - 文件路径（相对于工作区），默认为 `memory/MEMORY.md`

**安全防护：** 路径遍历攻击防护 —— 将请求路径与工作区路径解析为绝对路径后，验证结果路径是否以工作区路径为前缀。非工作区路径返回 403。

**响应示例：**

```json
{
  "content": "# Memory\n\n...",
  "path": "memory/MEMORY.md",
  "exists": true
}
```

文件不存在时返回 `{"content": "", "exists": false}`。

---

### GET /api/memory/search

搜索记忆。

**参数：**

| 参数 | 必需 | 默认 | 说明 |
|------|------|------|------|
| `q` | 是 | - | 搜索关键词 |
| `llm` | 否 | - | 设为 `1` 时使用 LLM 解读搜索结果 |

**搜索策略：**

1. 优先使用 FAISS 向量索引搜索（需要安装 `sentence-transformers` + `faiss-cpu`）
2. 若 FAISS 不可用或返回空结果，回退到 grep 关键词匹配

**LLM 解读：** 当 `llm=1` 时，将搜索结果拼入系统提示词，调用 LLM 分析查询与记忆片段的关联、用户原本想回忆的内容以及发现的模式。

**响应示例：**

```json
{
  "results": [
    {"source": "memory/notes.md", "heading": "项目计划", "text": "...", "score": 0.85}
  ],
  "interpretation": "用户正在寻找..."  // 仅 llm=1 时
}
```

---

### POST /api/memory/rebuild-index

重建 FAISS 向量索引、任务索引和技能索引。

**响应示例：**

```json
{
  "ok": true,
  "faiss_available": true,
  "chunks": 128,
  "tasks_chunks": 32,
  "skills_chunks": 8
}
```

若 FAISS 不可用，`faiss_available` 为 `false`，搜索将回退到 grep 模式。

---

### POST /api/memory/chat

AI 记忆聊天，基于检索到的记忆上下文进行对话。

**请求体：**

```json
{
  "message": "我上周做了什么？",
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

**处理流程：**

1. 通过 FAISS + grep 搜索相关工作区记忆和任务文件
2. 构建包含检索上下文的系统提示词
3. 调用 LLM 流式生成回复

**响应格式：** Server-Sent Events (SSE)

```
event: token
data: {"token": "根据..."}

event: token
data: {"token": "记忆..."}

event: sources
data: {"sources": [{"source": "memory/notes.md", "heading": "", "score": 0.85}]}

event: done
data: {}
```

事件类型：

| 事件 | 说明 |
|------|------|
| `token` | 流式输出的文本片段 |
| `sources` | 回复依赖的记忆来源列表 |
| `error` | 错误信息 |
| `done` | 流结束 |

---

### POST /api/shutdown

重启 gateway 进程。

**实现：** 先停止所有 proxy 子进程，然后通过子进程延迟 3 秒后重新启动 gateway，当前进程立即退出。

**响应：**

```json
{"ok": true, "message": "Gateway restarting"}
```

---

### POST /api/stop

停止 gateway 进程。

**实现：** 先停止所有 proxy 子进程，然后延迟退出。

**响应：**

```json
{"ok": true, "message": "Gateway shutting down"}
```

---

## WebUI 开发指南

### 布局说明

```
nanobot/web/                   源码目录
nanobot/web/index.html         当前 WebUI 生产版本（单文件 SPA）
nanobot/web/public/brand/      品牌资源（logo、图标、favicon 等）
nanobot/web/dist/              构建产物（由 gateway 静态服务）
```

当前 WebUI 为单文件 HTML 应用（`index.html`），包含完整的内联 CSS 和 JS。所有配置页面通过 JavaScript 动态渲染，无需构建步骤。

### 开发模式设置

项目同时维护了一套基于 Vite + React 18 + TypeScript + Tailwind 3 + shadcn/ui 的 WebUI 开发环境（参见 `nanobot/web/README.md`）。

如需使用 Vite 开发服务器：

1. 从源码安装 NanoBot：

```bash
pip install -e .
```

2. 启用 WebSocket 通道（用于开发模式下的实时通信）：

在 `~/.nanobot/config.json` 中添加：

```json
{
  "channels": {
    "websocket": { "enabled": true }
  }
}
```

3. 启动 gateway：

```bash
nanobot gateway
```

4. 启动 Vite 开发服务器：

```bash
cd nanobot/web
bun install        # 或 npm install
bun run dev
```

默认打开 `http://127.0.0.1:5173`，开发服务器会自动代理 API 请求到 gateway。

如 gateway 使用非默认端口，设置环境变量：

```bash
NANOBOT_API_URL=http://127.0.0.1:9000 bun run dev
```

### 构建生产版本

```bash
cd nanobot/web
bun run build
```

构建产物写入 `nanobot/web/dist/`。打包 Python wheel 前运行构建可包含最新 WebUI 资源。

### 品牌资源

品牌资源位于 `nanobot/web/public/brand/`：

| 文件 | 用途 |
|------|------|
| `nanobot_logo.webp` | WebP 格式 logo |
| `nanobot_logo.png` | PNG 格式 logo |
| `nanobot_icon.png` | 页面显示图标（36x36） |
| `nanobot_favicon_32.png` | 浏览器 favicon（32x32） |
| `nanobot_apple_touch.png` | Apple Touch 图标 |

资源通过 `/brand/` 路径静态服务（对应 `StaticFiles` mount）。

---

## 常见问题

### WebUI 页面加载失败

确认 gateway 正在运行：执行 `nanobot gateway`。如果 gateway 在后台运行但页面无法访问，检查端口是否被占用：

```bash
netstat -ano | findstr :18790
```

### 配置修改后需要重启

修改 `gateway host/port`、通道配置、工具开关等非热重载路径时，WebUI 底部会弹出 "Restart Required" 确认对话框。点击 "Restart Now" 会自动重启 gateway。

### 设置模式（Setup Mode）

如果未配置任何 Provider 的 API Key，gateway 会以设置模式启动，仅提供 WebUI 界面和 API 服务，不启动 Agent 循环。控制台会显示：

```
Running in setup mode — configure an API key in the WebUI Providers tab, then restart.
```

在 Providers 标签页配置 API Key 后，点击 "Apply" 并重启 gateway。

### 记忆搜索功能不可用

如果未安装 `sentence-transformers` 和 `faiss-cpu`，向量搜索不可用，WebUI 会回退到 grep 关键词匹配。安装命令：

```bash
pip install sentence-transformers faiss-cpu
```

注：这会同时安装 PyTorch（约 2GB）并在首次使用时下载模型（约 30MB）。

### WebSocket 连接失败

当前 WebUI 不直接使用 WebSocket。仅在 Vite 开发模式下，Vite 开发服务器会代理 WebSocket 流量。生产环境通过 `/api/` 前缀的 REST API 和 SSE (`/api/memory/chat`) 进行通信。

### Mac 上模型列表加载失败

部分提供商在 macOS 上加载模型列表时可能出现兼容性问题。可在 `tools.web.search.base_url` 或对应 Provider 的 `api_base` 中手动指定 API 地址。
