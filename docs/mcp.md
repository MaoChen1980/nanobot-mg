# MCP (Model Context Protocol) 支持

## 什么是 MCP

MCP (Model Context Protocol) 是 Anthropic 提出的开放协议，旨在为 AI 模型提供统一的上下文扩展接口。MCP 允许 AI 助手通过标准化的方式接入外部工具、数据源和交互模板，类似于"AI 应用的 USB-C 接口"。

通过 MCP，你可以让 NanoBot 连接各种外部服务，例如：

- **数据库查询** — 连接 MySQL、PostgreSQL、SQLite 等，让 AI 直接查询数据
- **文件系统** — 安全地读写特定目录下的文件
- **API 网关** — 调用 GitHub、Slack、Notion 等外部 API
- **搜索引擎** — 集成自定义搜索服务
- **开发工具** — 连接代码仓库、CI/CD 流水线等

## NanoBot 的 MCP 支持架构

NanoBot 的 MCP 集成采用**轻量级客户端**架构，运行在 AgentLoop 主循环中：

```
┌─────────────────────────────────────────────────────────────┐
│                     NanoBot AgentLoop                        │
│                                                              │
│  ┌───────────────┐    ┌──────────────────────────────────┐   │
│  │  ToolRegistry  │    │        MCP Client Layer           │   │
│  │  (原生工具)     │    │                                   │   │
│  │                │    │  ┌──────────────────────────┐     │   │
│  │  ┌──────────┐  │    │  │  MCP Server A (stdio)    │     │   │
│  │  │ web_     │  │    │  │  ├─ mcp_A_search         │     │   │
│  │  │ search   │  │    │  │  └─ mcp_A_read_file      │     │   │
│  │  ├──────────┤  │    │  ├──────────────────────────┤     │   │
│  │  │ exec_    │  │    │  │  MCP Server B (SSE)      │     │   │
│  │  │ shell    │  │    │  │  ├─ mcp_B_query_db       │     │   │
│  │  └──────────┘  │    │  │  └─ mcp_B_get_schema     │     │   │
│  │                │    │  └──────────────────────────┘     │   │
│  └───────┬───────┘    └──────────────────────────────────┘   │
│          │                        ▲                          │
│          │ 统一调用               │ 连接管理                  │
│          ▼                        │                          │
│  ┌─────────────────────────────────┐                        │
│  │     LLM Provider (模型)         │                        │
│  └─────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

核心组件：

| 组件 | 文件 | 职责 |
|------|------|------|
| `MCPServerConfig` | [schema.py](file:///e:/claude/nanobot-mg/nanobot/config/schema.py#L311-L321) | MCP 服务器连接配置模型 |
| `MCPToolWrapper` | [mcp.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L251-L280) | 将 MCP 工具封装为 NanoBot 原生工具 |
| `MCPResourceWrapper` | [mcp.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L283-L318) | 将 MCP 资源封装为只读工具 |
| `MCPPromptWrapper` | [mcp.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L321-L387) | 将 MCP 提示模板封装为只读工具 |
| `connect_mcp_servers` | [mcp.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L390-L583) | 连接所有配置的 MCP 服务器并注册能力 |
| `connect_mcp` | [loop_mcp.py](file:///e:/claude/nanobot-mg/nanobot/agent/loop_mcp.py#L14-L35) | 在 AgentLoop 中执行惰性连接 |

### 连接生命周期

MCP 连接是**惰性 (lazy)** 的——在 AgentLoop 启动时不会立即连接，而是在收到**第一条用户消息**时触发连接。连接成功后会保持长连接，直到 AgentLoop 关闭。

连接失败时不会阻塞消息处理，而是打印警告日志并在下一条消息时重试。

```
启动 → 收到第一条消息 → _connect_mcp() → 连接 MCP 服务器
                                           ├── 成功 → 注册工具，正常处理消息
                                           └── 失败 → 记录日志，下次重试
```

## 配置 MCP 服务器

在 `config.yml` 的 `tools.mcp_servers` 下配置 MCP 服务器，格式为字典，键为服务器名称，值为服务器配置。

### 配置字段说明

所有字段定义在 [MCPServerConfig](file:///e:/claude/nanobot-mg/nanobot/config/schema.py#L311-L321) 中：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | `"stdio"` / `"sse"` / `"streamableHttp"` | 自动检测 | 传输协议类型 |
| `command` | `str` | `""` | stdio 模式下要执行的命令 |
| `args` | `list[str]` | `[]` | stdio 模式下命令的参数列表 |
| `env` | `dict[str, str]` | `{}` | stdio 模式下额外环境变量 |
| `url` | `str` | `""` | HTTP/SSE 模式下的端点 URL |
| `headers` | `dict[str, str]` | `{}` | HTTP/SSE 模式下的自定义请求头 |
| `tool_timeout` | `int` | `30` | 工具调用超时时间（秒） |
| `enabled_tools` | `list[str]` | `["*"]` | 启用的工具列表，`["*"]` 表示全部启用 |

### 传输类型自动检测

如果不指定 `type`，NanoBot 会根据以下规则自动推断：

1. 如果设置了 `command`（非空字符串），则视为 `stdio`
2. 如果设置了 `url` 且以 `/sse` 结尾，则视为 `sse`
3. 如果设置了 `url` 但不以 `/sse` 结尾，则视为 `streamableHttp`

### enabled_tools 过滤规则

`enabled_tools` 字段控制哪些 MCP 能力被注册到 NanoBot 的 ToolRegistry 中：

- `["*"]`（默认值）— 注册该服务器的所有工具、资源和提示
- `[]` — 不注册任何能力（可以用于先连接但不启用）
- `["tool_name"]` — 只注册指定名称的工具，支持两种匹配方式：
  - 原始 MCP 名称：`tool_name`
  - 封装后名称：`mcp_<server>_<tool_name>`

如果指定的工具在 MCP 服务器中不存在，会在日志中打印警告并列出可用的工具名称。

## 支持的传输方式

### 1. stdio（标准输入输出）

通过子进程方式运行 MCP 服务器，NanoBot 通过 stdin/stdout 与该进程通信。

适用场景：本地运行的 MCP 服务器（如 `npx` 安装的 MCP 包）。

```
tools:
  mcp_servers:
    my_stdio_server:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed-dir"]
```

#### Windows 兼容性

在 Windows 上，对于 `npx`、`npm`、`pnpm`、`yarn`、`bunx` 以及 `.cmd` / `.bat` 脚本，NanoBot 会自动包装为通过 `cmd.exe /d /c <command>` 执行，以确保兼容性。具体实现见 [_normalize_windows_stdio_command](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L73-L101)。

### 2. SSE (Server-Sent Events)

通过 HTTP SSE 协议连接远程 MCP 服务器。

适用场景：作为 HTTP 服务运行的 MCP 服务器（通常以 `/sse` 为端点）。

```
tools:
  mcp_servers:
    my_sse_server:
      type: "sse"
      url: "http://localhost:8080/sse"
      headers:
        Authorization: "Bearer <token>"
```

连接前，NanoBot 会通过 TCP 快速探测目标地址是否可达。

### 3. Streamable HTTP

MCP 的另一种 HTTP 传输方式，使用 HTTP POST 请求而非 SSE 流。

适用场景：需要更灵活的双向通信，或 MCP 服务器不支持 SSE 时。

```
tools:
  mcp_servers:
    my_http_server:
      type: "streamableHttp"
      url: "http://localhost:8080/mcp"
      headers:
        X-API-Key: "<your-api-key>"
```

## 工具注册机制

MCP 服务器的工具、资源和提示会通过以下方式注册为 NanoBot 的原生工具：

### 命名规则

| MCP 能力类型 | NanoBot 工具名称格式 | 示例 |
|-------------|-------------------|------|
| 工具 (Tool) | `mcp_<server>_<tool_name>` | `mcp_my_server_sql_query` |
| 资源 (Resource) | `mcp_<server>_resource_<resource_name>` | `mcp_my_server_resource_docs` |
| 提示 (Prompt) | `mcp_<server>_prompt_<prompt_name>` | `mcp_my_server_prompt_code_review` |

### 名称净化

MCP 工具名称中任何不在 `[a-zA-Z0-9_-]` 范围内的字符都会被替换为下划线 `_`，连续下划线会被合并。具体实现见 [_sanitize_name](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L40-L42)。

### 注册流程

在 `connect_mcp_servers` 函数中（[mcp.py:L390-L583](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L390-L583)）：

1. 根据配置选择传输方式，建立连接并初始化 `ClientSession`
2. 调用 `session.list_tools()` 获取服务器提供的工具列表
3. 对每个工具创建 `MCPToolWrapper` 实例，注册到 `ToolRegistry`
4. 尝试调用 `session.list_resources()` 获取资源列表（可能失败，某些服务器不支持）
5. 对每个资源创建 `MCPResourceWrapper` 实例（只读工具），注册到 `ToolRegistry`
6. 尝试调用 `session.list_prompts()` 获取提示模板列表（可能失败）
7. 对每个提示模板创建 `MCPPromptWrapper` 实例（只读工具），注册到 `ToolRegistry`

### MCPToolWrapper 详解

[MCPToolWrapper](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L251-L280) 是核心的封装类：

- 继承自 `MCPWrapperBase`（[mcp.py:L165-L248](file:///e:/claude/nanobot-mg/nanobot/agent/tools/mcp/mcp.py#L165-L248)）
- 实现了重试机制：遇到 transient 错误（如连接重置、管道断开等）会自动重试一次
- 超时控制：通过 `asyncio.wait_for` 实现，超时时间由 `tool_timeout` 配置
- Input Schema 标准化：`_normalize_schema_for_openai` 处理可为空的 JSON Schema 模式（nullable unions）
- 结果处理：将 `TextContent` 等 MCP 类型格式化为纯文本字符串

所有封装后的工具与 NanoBot 的原生工具（如 `web_search`、`exec_shell`）平级注册到同一个 `ToolRegistry` 中，LLM 可以无差别地调用它们。

## 配置示例

### 连接 PostgreSQL 数据库

```yaml
tools:
  mcp_servers:
    postgres:
      command: "npx"
      args: ["-y", "@anthropic/mcp-postgres", "postgresql://user:password@localhost:5432/mydb"]
      tool_timeout: 60
```

### 连接 SQLite 数据库

```yaml
tools:
  mcp_servers:
    sqlite:
      command: "uvx"
      args: ["mcp-server-sqlite", "--db-path", "/data/mydb.sqlite"]
```

### 连接 GitHub API

```yaml
tools:
  mcp_servers:
    github:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "ghp_xxxxxxxxxxxxxxxxxxxx"
```

### 连接自定义 HTTP MCP 服务器 (SSE)

```yaml
tools:
  mcp_servers:
    my_custom_api:
      type: "sse"
      url: "http://192.168.1.100:8000/sse"
      headers:
        Authorization: "Bearer my-token"
      tool_timeout: 60
      enabled_tools: ["search_docs", "mcp_my_custom_api_get_weather"]
```

### 连接远程 Streamable HTTP MCP 服务器

```yaml
tools:
  mcp_servers:
    remote_service:
      type: "streamableHttp"
      url: "https://mcp.example.com/endpoint"
      headers:
        Authorization: "Bearer <token>"
```

### 只启用特定工具

```yaml
tools:
  mcp_servers:
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
      enabled_tools:
        - "read_file"       # 只注册这个原始名称的工具
        - "write_file"      # 和这个
```

## 故障排查

### 连接失败

**现象**：启动后日志中出现 `MCP server 'xxx': failed to connect`。

**可能原因和解决**：

1. **命令不存在或路径错误**：
   - 确保 `npx` / `uvx` 等命令已安装且在 PATH 中
   - 在终端中手动执行命令验证

2. **SSE/HTTP 地址不可达**：
   - 检查 URL 拼写和端口号
   - 确认服务器已启动运行
   - NanoBot 会先通过 TCP 探测 (`_probe_http_url`) 检查连通性，如果探测失败则不继续连接

3. **环境变量缺失**：
   - `env` 配置中遗漏了必要的环境变量（如 API Token）
   - 检查 MCP 服务器文档所需的环境变量

4. **协议错误**（常见于 stdio，日志中带有 `parse error` / `invalid json` / `jsonrpc` 等关键词）：
   - 提示：`This looks like stdio protocol pollution. Make sure the MCP server writes only JSON-RPC to stdout and sends logs/debug output to stderr instead.`
   - **MCP 协议要求服务器仅通过 stdout 输出 JSON-RPC 消息，日志和调试信息必须输出到 stderr**
   - 确保 MCP 服务器实现正确

### 工具未注册

**现象**：LLM 无法找到预期的 `mcp_xxx` 工具。

**可能原因和解决**：

1. **enabled_tools 过滤**：检查 `enabled_tools` 列表是否包含了该工具。如果不确定，先设为 `["*"]` 查看全部可用工具。
2. **名称不匹配**：检查 `enabled_tools` 中的名称是否与服务器实际提供的工具名称一致。NanoBot 会在日志中打印可用工具列表。
3. **不支持资源/提示**：某些 MCP 服务器不支持 `list_resources()` 或 `list_prompts()`，这属于正常情况，不会影响工具注册。

### 工具调用超时

**现象**：调用 MCP 工具时返回 `"(MCP tool call timed out after 30s)"`。

**解决**：
- 增大 `tool_timeout` 配置值（默认 30 秒）
- 检查 MCP 服务器是否处于高负载状态
- 某些数据库查询可能需要更长的超时时间

### 连接过程中断（CancelledError）

**现象**：日志出现 `MCP connection cancelled (will retry next message)`。

**原因**：AgentLoop 关闭时取消了正在进行的 MCP 连接。这是正常行为，下次收到消息时会自动重试。

## 安全注意事项

### 1. 命令注入风险

`stdio` 模式下，NanoBot 会直接执行 `command` 和 `args` 中指定的命令。确保：

- 这些值来自可信的配置文件（`config.yml`）
- 不要从用户输入动态构造 MCP 服务器命令

### 2. 环境变量安全

`env` 中的敏感信息（API Token、密码等）：

- 建议使用环境变量引用或密钥管理服务
- 配置文件应设置正确的文件权限（如 Unix `chmod 600`）
- 不要将含敏感信息的配置文件提交到版本控制系统

### 3. HTTP 传输安全

- 使用 `https://` URL 确保传输加密
- 避免在查询参数中传递敏感信息
- 对 SSE 和 Streamable HTTP 传输使用适当的认证头

### 4. Windows 特殊说明

在 Windows 上，NanoBot 会自动将 `npx` / `npm` / `pnpm` / `yarn` / `bunx` 及 `.cmd` / `.bat` 脚本包装为通过 `cmd.exe /d /c` 执行。这是为了兼容性，但需要注意：

- 被包装的命令可能继承额外的环境变量
- 路径中含空格时需注意引号的使用

### 5. 资源访问控制

- 文件系统类 MCP 服务器（如 `server-filesystem`）应限制可访问的目录范围
- 数据库类 MCP 服务器应使用只读账号，除非明确需要写权限
- 定期审查 `enabled_tools` 列表，最小化暴露给 LLM 的能力范围

### 6. 网络隔离

- 对于 SSE/Streamable HTTP 传输，连接目标应为受信任的内网服务或经过认证的外网服务
- 使用 `headers` 配置添加 API Key 或 Token 认证
- 注意不要向不受信任的 MCP 服务器暴露敏感信息
