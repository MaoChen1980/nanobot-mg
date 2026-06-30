# My 工具（SelfTool / check_config）

My 工具是 NanoBot 中让 AI 代理查看和修改代理循环（AgentLoop）运行时配置的工具。通过它可以管理用户个人信息、调整运行时参数、存储草稿笔记等。

## 工具标识

- **名称**：`check_config`
- **所属类**：`SelfTool`
- **代码位置**：[self.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/self.py)

## 功能说明

`SelfTool` 提供两种操作：

| 操作 | 别名 | 功能 |
|---|---|---|
| `check` / `inspect` | 查看 | 查看运行时状态或特定配置 |
| `set` / `modify` | 设置 | 修改运行时设置或存储笔记 |

### 查看操作

**无参数**：显示完整运行时概览，包含：

- RESTRICTED 键值（`max_iterations`、`context_window_tokens`、`model`）
- 其他顶层属性（workspace、provider_retry_mode、web_config、exec_config、subagents）
- Token 使用情况（`_last_usage`）
- 草稿板（`_runtime_vars`，即 scratchpad）
- 工作区钩子（hooks）
- 可用技能列表（skills）

**带参数 `key`**：检查特定属性，支持点号路径访问（如 `exec_config.sandbox_enabled`）

**特殊别名**：
- `scratchpad` — 映射到 `_runtime_vars`
- `shell` — 映射到 `exec_config`

### 设置操作

设置操作受 `modify_allowed` 标志控制（由配置 `tools.config.allow_set` 决定）。

支持三种设置模式：

1. **限制字段**（`RESTRICTED`）：
   - `max_iterations` — int，范围 1-100
   - `context_window_tokens` — int，范围 4096-1000000
   - `model` — string，至少 1 字符（会尝试通过 `switch_model()` 切换提供者）

2. **自由字段**：AgentLoop 已有的属性，类型需兼容

3. **草稿板存储**：不存在的键会存入 `_runtime_vars`（最多 64 个键），存储 JSON 安全的值

## 配置结构

配置存在于代理循环（AgentLoop）的运行时状态中。以下为可通过 `check_config` 查看和管理的字段：

### 示例配置结构

```json
{
  "max_iterations": 30,
  "context_window_tokens": 128000,
  "model": "claude-sonnet-4-20250514",
  "workspace": "/path/to/workspace",
  "provider_retry_mode": "simple",
  "max_tool_result_chars": 20000,
  "_current_iteration": 5,
  "web_config": { ... },
  "exec_config": { ... },
  "subagents": { ... },
  "_last_usage": { ... },
  "_runtime_vars": {
    "user_name": "张三",
    "preferred_language": "中文"
  }
}
```

### 用户信息存储方式

用户个人信息存储在 `_runtime_vars`（scratchpad）中。典型的用户信息键包括：

| 键 | 说明 | 示例值 |
|---|---|---|
| `name` | 用户姓名 | `"张三"` |
| `bio` | 用户简介 | `"Python 开发者，擅长后端"` |
| `preferences` | 用户偏好 | `{"language": "zh-CN", "verbosity": "concise"}` |
| `skills` | 用户技能列表 | `["Python", "Docker", "Kubernetes"]` |

这些值可通过 `check_config(action='set', key='name', value='张三')` 设置，通过 `check_config(action='check', key='name')` 查看。

### 配置访问限制

`SelfTool` 定义了多个安全层级，防止代理访问或修改敏感配置：

**BLOCKED**（完全不可访问）：
- `bus`, `provider`, `_running`, `tools` — 核心基础设施
- `_runtime_vars` — 配置管理
- `runner`, `sessions`, `context`, `commands` — 子系统
- `_mcp_servers`, `_mcp_stacks` — MCP 服务器
- `_session_dispatch`, `_session_locks`, `_background_tasks` — 会话/任务追踪
- `restrict_to_workspace`, `channels_config` — 安全边界
- `_concurrency_gate`, `_extra_hooks` — 内部机制

**READ_ONLY**（可查看，不可修改）：
- `subagents` — 子代理状态
- `_current_iteration` — 当前迭代次数
- `exec_config` — 执行配置
- `web_config` — Web 配置

**DENIED_ATTRS**（Python 内部属性）：
- `__class__`, `__dict__`, `__init__`, `__code__` 等魔术方法

**SENSITIVE_NAMES**（敏感字段名）：
- `api_key`, `secret`, `password`, `token`, `credential`, `private_key`, `access_token`, `refresh_token`, `auth`

点路径访问时，路径中的每个部分都会经过以上安全检查。

## 启用配置

`SelfTool` 的修改能力由 `tools.config.allow_set` 配置控制：

```
tools:
  config:
    allow_set: true   # 允许修改配置
    # allow_set: false  # 只读模式
```

当 `allow_set` 为 `false` 时，`set`/`modify` 操作会返回错误信息：`"Error: set is disabled (tools.config.allow_set is false)"`。

## 使用示例

### 查看完整运行时状态

```
action: "check"
```

返回所有关键配置的概览。

### 查看特定配置

```
action: "check", key: "max_iterations"
```

返回：`max_iterations: 30`

### 设置运行时参数

```
action: "set", key: "max_iterations", value: 50
```

返回：`Set max_iterations = 50 (was 30)`

### 存储用户信息到草稿板

```
action: "set", key: "user_name", value: "张三"
```

返回：`Set scratchpad.user_name = '张三'`

### 切换模型

```
action: "set", key: "model", value: "claude-4-haiku"
```

框架会自动调用 `switch_model()` 查找可用提供者。如果找不到匹配的模型返回错误。

### 查看子代理状态

```
action: "check", key: "subagents"
```

返回子代理列表，包括每个代理的阶段、迭代次数、运行时间和工具使用情况。
