# HTTP API 文档

nanobot gateway 内嵌了一个 REST API 服务，提供 WebUI 所需的配置管理、记忆搜索、文件读取等功能。API 与 WebUI 同端口（默认 `18790`）。

> 注意：此 API 是 gateway 的管理接口，**并非 OpenAI 兼容 API**。nanobot 目前不提供独立的 `/v1/chat/completions` 端点。

---

## 目录

- [启动方式](#启动方式)
- [认证](#认证)
- [端点列表](#端点列表)
  - [GET /health](#get-health)
  - [GET /api/settings](#get-apisettings)
  - [PUT /api/settings/update](#put-apisettingsupdate)
  - [GET /api/config](#get-apiconfig)
  - [PUT /api/config](#put-apiconfig)
  - [GET /api/provider-models](#get-apiprovider-models)
  - [GET /api/workspace/file](#get-apiworkspacefile)
  - [GET /api/memory/search](#get-apimemorysearch)
  - [POST /api/memory/rebuild-index](#post-apimemoryrebuild-index)
  - [POST /api/memory/chat](#post-apimemorychat)
  - [POST /api/shutdown](#post-apishutdown)
  - [POST /api/stop](#post-apistop)
- [WebUI 访问](#webui-访问)
- [文件上传](#文件上传)

---

## 启动方式

API 内嵌于 gateway 中，启动 gateway 即可访问：

```bash
nanobot gateway
```

默认监听 `0.0.0.0:18790`。WebUI 和 API 在同一端口提供服务。

---

## 认证

API 默认**不设认证**。nanobot 定位为个人/本地助手，所有 API 调用直接可用。

如果需要在公网暴露，建议在反向代理层面（如 Nginx、Caddy）添加认证。

---

## 端点列表

### GET /

WebUI 主页。返回 `index.html`。

---

### GET /health

健康检查端点。

**响应：**

```json
{
  "status": "ok"
}
```

HTTP 状态码：`200`

---

### GET /api/settings

获取当前设置的摘要信息（仅包含 UI 需要的字段，非完整配置）。

**响应：**

```json
{
  "agent": {
    "model": "gpt-4o",
    "provider": "auto",
    "resolved_provider": "openai",
    "has_api_key": true
  },
  "providers": [
    {
      "name": "openai",
      "label": "OpenAI"
    }
  ],
  "runtime": {
    "config_path": ""
  },
  "requires_restart": false
}
```

---

### PUT /api/settings/update

更新特定设置项（model 和 provider）。

**请求体：**

```json
{
  "model": "gpt-4o",
  "provider": "openai"
}
```

**响应：**

```json
{
  "agent": {
    "model": "gpt-4o",
    "provider": "openai",
    "resolved_provider": "openai",
    "has_api_key": true
  },
  "providers": [...],
  "runtime": {"config_path": ""},
  "requires_restart": true
}
```

`requires_restart` 指示是否需要重启 gateway 才能生效。

---

### GET /api/config

获取完整的配置文件内容（`config.json` 的解析结果）。

**响应：** 完整的 `Config` schema JSON。

---

### PUT /api/config

保存完整的配置文件。请求体中需包含完整的配置 JSON。

**请求体：**

```json
{
  "agents": {
    "defaults": {
      "model": "gpt-4o",
      "provider": "openai"
    }
  },
  ...
}
```

**响应：**

```json
{
  "ok": true
}
```

该端点会自动触发运行中 gateway 的热重载（hot-reload），无需重启即可使部分配置项生效。

如果渠道配置中有渠道被禁用，对应的 proxy 进程会被自动停止。

**错误响应：**

```json
{
  "error": "Validation failed: ..."
}
```

HTTP 状态码：`400`

---

### GET /api/provider-models

从指定的 LLM 提供商 API 获取模型列表。

**查询参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `provider` | string | 是 | 提供商名称，如 `openai`、`anthropic`、`deepseek` |

**支持的提供商列表：**

`openai`, `anthropic`, `deepseek`, `minimax`, `minimax_anthropic`, `minimax_cn`, `minimax_anthropic_cn`, `moonshot`, `groq`, `ollama`, `gemini`, `openrouter`, `aihubmix`, `siliconflow`, `dashscope`, `mistral`, `hunyuan`, `minicpm`, `xai`

**响应：**

```json
{
  "models": ["gpt-4o", "gpt-4-turbo", ...]
}
```

如提供商未配置 API Key，返回空列表。

---

### GET /api/workspace/file

读取工作区中的 Markdown 文件。

**查询参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `path` | string | 否 | 文件相对路径，默认为 `memory/MEMORY.md` |

**路径安全：** 该端点有路径穿越防护，只允许访问工作区目录内的文件。

**响应：**

```json
{
  "content": "# Memory File\n\n...",
  "path": "memory/MEMORY.md",
  "exists": true
}
```

文件不存在时返回 `{"content": "", "exists": false}`。

---

### GET /api/memory/search

搜索记忆库。优先使用 FAISS 向量搜索，回退到 grep 关键词搜索。

**查询参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `q` | string | 是 | 搜索查询 |
| `llm` | string | 否 | 设为 `"1"` 时使用 LLM 对结果进行解读 |

**响应：**

```json
{
  "results": [
    {
      "source": "memory/projects.md",
      "heading": "Project Alpha",
      "text": "相关文本片段...",
      "score": 0.85
    }
  ]
}
```

`llm=1` 时额外返回 `interpretation` 字段：

```json
{
  "results": [...],
  "interpretation": "LLM 对搜索结果的解读..."
}
```

---

### POST /api/memory/rebuild-index

重建 FAISS 向量索引。该操作在后台线程中执行，会扫描工作区中的 `memory/`、`tasks/`、`skills/` 目录。

**响应：**

```json
{
  "ok": true,
  "faiss_available": true,
  "chunks": 150,
  "tasks_chunks": 30,
  "skills_chunks": 10
}
```

未安装 `sentence-transformers` 和 `faiss-cpu` 时，`faiss_available` 为 `false`。

---

### POST /api/memory/chat

基于记忆的 AI 对话（SSE 流式响应）。该端点在 `/api/memory/search` 的基础上自动检索相关记忆作为上下文，并用 LLM 生成回答。

**请求体：**

```json
{
  "message": "我之前的项目计划是什么？",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么可以帮助你的？"}
  ]
}
```

**响应：** Server-Sent Events (SSE) 流

```
event: token
data: {"token": "你的"}

event: token
data: {"token": "项目"}

event: token
data: {"token": "计划"}

event: sources
data: {"sources": [{"source": "memory/projects.md", "heading": "", "score": 0.85, "text": "..."}]}

event: done
data: {}
```

| 事件类型 | 说明 |
|---------|------|
| `token` | LLM 生成的文本片段 |
| `sources` | 检索到的记忆来源列表 |
| `error` | 错误信息 |
| `done` | 流结束标记 |

---

### POST /api/shutdown

重启 gateway 进程。会先停止所有 proxy 子进程，然后启动新的 gateway 进程。

**响应：**

```json
{
  "ok": true,
  "message": "Gateway restarting"
}
```

注意：调用后当前进程会立即退出，新的进程会在延迟后启动。

---

### POST /api/stop

停止 gateway 进程。会先停止所有 proxy 子进程，然后退出。

**响应：**

```json
{
  "ok": true,
  "message": "Gateway shutting down"
}
```

---

## WebUI 访问

启动 gateway 后，打开浏览器访问：

```
http://127.0.0.1:18790/
```

WebUI 是一个内嵌的单页应用（SPA），提供：
- **Settings 标签页**：配置 AI 模型、渠道、提供商、工具、Gateway 设置
- **Memory 标签页**：浏览和搜索记忆文件，AI 对话式记忆查询

所有配置操作通过上述 API 端点完成。

---

## 文件上传

当前 API 不提供文件上传端点。媒体文件（图片、附件等）在渠道消息中以 base64 data URL 形式处理，由 `nanobot.utils.media_decode` 模块解码并保存到工作区临时目录。
