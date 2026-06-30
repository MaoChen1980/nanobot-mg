# NanoBot 安全架构文档

## 1. 安全架构概述

NanoBot 采用**多层纵深防御**策略，在多个层面构建安全屏障：

| 层级 | 防护内容 | 核心模块 |
|------|----------|----------|
| 网络层 | SSRF 防护、内网地址阻断、CIDR 白名单 | `nanobot/security/network.py` |
| Shell 执行层 | 危险命令检测、路径穿越防护、工作区边界限制、命令白名单 | `nanobot/agent/tools/shell_validators.py` |
| 沙箱隔离层 | bubblewrap 容器化执行（Linux） | `nanobot/agent/tools/sandbox.py` |
| 文件系统层 | 路径解析验证、设备文件阻断、覆盖写入保护 | `nanobot/agent/tools/filesystem/filesystem_base.py` |
| 危险操作检测层 | 标准化告警格式、覆盖写入检测、force push 阻断 | `nanobot/agent/tools/danger.py` |
| 运行时自省保护 | Agent 运行时属性只读/黑名单保护 | `nanobot/agent/tools/self.py` |
| API 层 | 路径穿越防护、配置校验 | `nanobot/api/server.py` |
| 配置层 | 密钥通过环境变量引用、SSRF 白名单配置 | `nanobot/config/schema.py`, `nanobot/config/loader.py` |

核心安全机制采用**"警告而非错误"**的设计模式：检测到危险操作时返回格式化的 `danger_warning` 告警，LLM 在确认安全后可通过 `danger_override=true` 参数覆盖。该覆盖仅对单次调用生效。

---

## 2. 网络策略 / SSRF 保护

### 2.1 内网地址黑名单

位于 [nanobot/security/network.py](file:///e:/claude/nanobot-mg/nanobot/security/network.py) 的 `_BLOCKED_NETWORKS` 定义了默认阻断的 IP 段：

| 网段 | 用途 |
|------|------|
| `0.0.0.0/8` | 当前网络（自标识） |
| `10.0.0.0/8` | RFC 1918 私有 A 类 |
| `100.64.0.0/10` | 运营商级 NAT（CGNAT） |
| `127.0.0.0/8` | 本地回环地址 |
| `169.254.0.0/16` | 链路本地地址 / 云元数据服务 |
| `172.16.0.0/12` | RFC 1918 私有 B 类 |
| `192.168.0.0/16` | RFC 1918 私有 C 类 |
| `::1/128` | IPv6 本地回环 |
| `fc00::/7` | IPv6 唯一本地地址 |
| `fe80::/10` | IPv6 链路本地地址 |

### 2.2 CIDR 白名单（allowFrom）

通过配置文件中的 `tools.ssrf_whitelist` 字段（CIDR 列表）可添加例外规则，例如允许 Tailscale 使用的 `100.64.0.0/10`。配置加载时由 [nanobot/config/loader.py](file:///e:/claude/nanobot-mg/nanobot/config/loader.py) 的 `_apply_ssrf_whitelist()` 调用 `configure_ssrf_whitelist()` 注入白名单。

```python
# config.json 示例
{
  "tools": {
    "ssrfWhitelist": ["100.64.0.0/10"]
  }
}
```

白名单优先级高于黑名单：如果目标 IP 命中任意白名单网段，则直接放行；否则继续检查黑名单。

### 2.3 URL 验证流程

`validate_url_target(url)` 对外部 URL 执行完整验证：

1. **协议检查**：仅允许 `http` 和 `https`
2. **Hostname 检查**：URL 必须包含有效的 hostname
3. **DNS 解析**：异步解析 hostname（10 秒超时）
4. **IP 校验**：对每个解析结果逐一检查，任一 IP 命中黑名单则阻断
5. **重定向后校验**：`validate_resolved_url()` 在重定向后再次校验目标 IP

Shell 命令中的 URL 通过 `targets_internal_address(command, allow_loopback=False)` 检查。该函数使用正则提取命令中的 `http://`/`https://` URL，进行 DNS 解析和 IP 校验。Shell 执行场景中回环地址（127.0.0.0/8、::1）默认允许，因为 agent 常需要访问本地启动的服务。

---

## 3. Shell 命令安全

位于 [nanobot/agent/tools/shell_validators.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/shell_validators.py) 的命令安全校验入口为 `check_command_safety()`，执行以下检查链：

### 3.1 危险命令模式

`DANGEROUS_PATTERNS` 列表定义了被自动阻断的命令模式：

| 模式 | 风险说明 |
|------|----------|
| `rm -rf` / `rm -fr` | 递归强制删除 |
| `del /f` / `del /q`（Windows） | 强制/安静删除 |
| `rmdir /s`（Windows） | 递归删除目录 |
| `format`（命令起始） | 格式化磁盘 |
| `mkfs` / `diskpart` | 磁盘分区操作 |
| `dd if=` | 块设备写入 |
| `>/dev/sd` | 块设备写入 |
| `shutdown` / `reboot` / `poweroff`（命令起始） | 系统电源管理 |
| Fork bomb `:(){ };:` | 拒绝服务攻击 |
| `git push --force` | 强制推送，丢失远程历史 |
| `git reset --hard` | 硬重置，丢失未提交更改 |
| `git clean -f` | 删除未跟踪文件 |
| `pip uninstall` | 卸载 Python 包 |

### 3.2 命令白名单

如果配置了 `allow_patterns`，命令必须匹配至少一个允许的正则模式，否则被拒绝。此功能提供白名单模式，适合限制 agent 只能执行特定类型的命令。

### 3.3 内网 URL 检测

`_check_internal_url(command)` 复用网络模块的 `targets_internal_address()`，检测命令中是否包含指向内网地址的 URL。回环地址在此处**默认放行**（`allow_loopback=True`）。

### 3.4 路径穿越防护

`_check_path_traversal(command, restrict_to_workspace)` 在启用 `restrict_to_workspace` 时，检测命令中是否包含 `../` 或 `..\\` 等目录穿越字符。

### 3.5 工作区边界检查

`_check_workspace_boundary(command, cwd, workspace_root, restrict_to_workspace)` 提取命令中的所有路径（Windows 绝对路径、POSIX 绝对路径、`~` 开头的路径），逐一验证是否位于工作区目录或 media 目录之下。任何越界路径都会触发阻断。

### 3.6 环境变量安全

`ExecToolConfig` 中的 `allowed_env_keys` 列表控制哪些环境变量可以传递给子进程，防止敏感环境变量意外泄露到 shell 执行环境中。

---

## 4. 沙箱执行

### 4.1 bubblewrap 沙箱（Linux）

位于 [nanobot/agent/tools/sandbox.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/sandbox.py) 的 `_bwrap()` 函数使用 [bubblewrap](https://github.com/containers/bubblewrap) 在 Linux 上提供容器化执行环境。

沙箱配置要点：

| 配置项 | 说明 |
|--------|------|
| `--new-session` | 新 session，隔离信号传递 |
| `--die-with-parent` | 父进程退出时自动销毁 |
| `--ro-bind /usr` | 只读挂载系统库 |
| `--proc /proc` | 挂载 procfs |
| `--dev /dev` | 挂载 devfs |
| `--tmpfs /tmp` | 独立临时目录 |
| `--tmpfs <workspace_parent>` | 隐藏配置文件父目录 |
| `--bind <workspace>` | 可读写的工作区目录 |
| `--ro-bind-try <media>` | 只读的 media 目录 |

工作区目录是沙箱内唯一可读写的位置。配置文件所在的父目录被临时文件系统（tmpfs）遮蔽，防止 agent 访问或修改配置文件。media 目录以只读方式挂载，供命令读取上传的附件。

### 4.2 沙箱后端扩展

沙箱采用可扩展的后端注册机制，当前仅支持 `bwrap`。通过 `wrap_command(sandbox, command, workspace, cwd)` 调用，在配置中通过 `tools.exec.sandbox` 字段选择后端（空字符串表示不使用沙箱）。

---

## 5. 危险操作检测

### 5.1 标准化告警格式

位于 [nanobot/agent/tools/danger.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/danger.py) 的 `danger_warning()` 函数生成统一的危险操作告警：

```
⚠️ Danger: <问题描述>
  Risk: <风险说明>
  Suggestion: <替代方案建议>
  To proceed anyway, re-call <工具名> with danger_override=true
```

此格式的关键设计：不以 "Error" 开头，LLM 框架将其视为普通工具返回而非错误，LLM 可自行判断是否覆盖继续执行。

### 5.2 覆盖写入检测

`check_overwrite_danger(fp, was_read, size_bytes)` 在以下同时满足时告警：
- 目标文件已存在
- LLM 未读取过该文件（`file_state.check_read()` 返回警告）
- 文件大于 1 KB

这防止 LLM 在未确认文件内容的情况下盲目覆盖。

### 5.3 文件写入验证

`write_file` 工具在写入后自动执行验证：
- 从写入内容中提取第一个非注释行作为模式进行搜索验证
- 对 Python 文件自动执行语法检查（`py_compile`）
- 支持 `then_check` 类型检查（pyright/tsc）

---

## 6. 文件系统安全

### 6.1 路径解析与访问控制

位于 [nanobot/agent/tools/filesystem/filesystem_base.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/filesystem/filesystem_base.py) 的 `_resolve_path()` 是所有文件系统工具的路径解析入口：

1. **拒绝相对路径**：所有路径必须为绝对路径
2. **`$WORKSPACE` 变量检查**：如果路径包含 `$WORKSPACE` 则给出明确的解析提示
3. **目录约束检查**：如果配置了 `allowed_dir`，解析后的路径必须在允许目录（工作区 + media 目录 + 额外允许目录）之下
4. **`PermissionError`**：越界访问抛出 `PermissionError`，而非静默失败

### 6.2 设备文件阻断

`_BLOCKED_DEVICE_PATHS` 阻止 agent 读取可能产生无限输出或挂起的设备文件：

- POSIX: `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/full`, `/dev/stdin`, `/dev/stdout`, `/dev/stderr`, `/dev/tty`, `/dev/console`, `/dev/fd/[012]`
- Windows: `CON`, `NUL`, `AUX`, `PRN`, `CONIN$`, `CONOUT$`, `COM[1-9]`, `LPT[1-9]`, `\\.\` 前缀的 NT 命名空间路径

同时检查原始路径和符号链接解析后的目标路径。

### 6.3 API 层路径穿越防护

位于 [nanobot/api/server.py](file:///e:/claude/nanobot-mg/nanobot/api/server.py) 的 `handle_workspace_file()` 在提供工作区文件访问时，校验 `path` 参数是否包含路径穿越：

```python
resolved = (workspace / file_path).resolve()
if not str(resolved).startswith(str(workspace.resolve())):
    return JSONResponse({"error": "Access denied"}, status_code=403)
```

### 6.4 工作区限制开关

配置项 `tools.restrict_to_workspace`（布尔值，默认 `false`）控制所有文件系统工具是否限制在工作目录内。启用后：
- Shell 命令中的路径穿越（`../`、`..\\`）被阻断
- Shell 命令访问工作区外的路径被阻断
- 文件系统工具的路径解析强制在工作区范围内

---

## 7. 配置安全

### 7.1 密钥管理

API Key 等敏感信息通过以下方式管理：
- **环境变量优先**：配置支持 `${VAR_NAME}` 语法引用环境变量，由 `resolve_config_env_vars()` 在加载时解析。未设置的环境变量引用会抛出 `ValueError`
- **Pydantic 环境变量前缀**：所有配置项可通过 `NANOBOT__` 环境变量覆盖（如 `NANOBOT__PROVIDERS__OPENAI__API_KEY`）
- **本地 Provider 免密钥**：Ollama、LM Studio、OVMS 等本地 provider 不要求 API Key
- **OAuth Provider**：openai_codex、github_copilot 等 OAuth provider 的 API Key 字段标记为 `exclude=True`，不序列化到配置文件

### 7.2 敏感字段保护

配置 schema 中明确标记了敏感字段：`openai_codex` 和 `github_copilot` 的 `ProviderConfig` 字段使用 `exclude=True`，防止 `model_dump()` 时序列化到外部。

### 7.3 运行时自省保护

[SelfTool](file:///e:/claude/nanobot-mg/nanobot/agent/tools/self.py) 提供了 Agent 运行时状态的自省能力，通过以下机制保护敏感属性：

| 安全机制 | 内容 |
|----------|------|
| `BLOCKED`（阻止读写） | `bus`, `provider`, `tools`, `_mcp_servers`, `restrict_to_workspace`, `channels_config`, `_concurrency_gate` 等 |
| `READ_ONLY`（只读） | `subagents`, `exec_config`, `web_config` |
| `_DENIED_ATTRS`（阻止访问 Python 内部属性） | `__class__`, `__dict__`, `__globals__`, `__code__` 等魔术属性和内部方法 |

`allow_set` 配置项（默认 `false`）控制 LLM 是否可以通过 `config` 工具修改运行时状态。

### 7.4 配置文件存储

配置文件默认位于 `~/.nanobot/config.json`，可通过 `set_config_path()` 切换。加载失败时使用默认配置并记录警告，不会导致服务崩溃。

---

## 8. 安全最佳实践

### 8.1 部署建议

1. **启用工作区限制**：在配置中设置 `tools.restrictToWorkspace: true`，将 agent 的文件访问限制在工作区目录内
2. **使用沙箱执行**：在 Linux 上设置 `tools.exec.sandbox: "bwrap"`，为 shell 命令提供容器隔离
3. **配置 SSRF 白名单**：根据实际需要配置 `tools.ssrfWhitelist`，避免不必要地开放内网访问
4. **限制命令白名单**：如需严格限制 agent，配置 `tools.exec.allowed` 命令白名单
5. **关闭不需要的工具**：使用 `disabledSkills` 禁用不需要的 skill

### 8.2 API Key 管理

1. 优先使用环境变量注入 API Key（`${OPENAI_API_KEY}`）
2. 配置文件本身不应包含明文的 API Key
3. 使用 `NANOBOT_` 前缀的环境变量进行配置覆盖
4. 定期轮换 API Key

### 8.3 网络防护

1. 默认阻断所有 RFC 1918 内网地址的 SSRF 请求
2. 仅在需要时通过 `ssrfWhitelist` 开放特定内网网段
3. 使用 `web_fetch` 而非 `exec curl/wget` 进行 HTTP 请求，以自动获得 SSRF 保护
4. 网关监听 `127.0.0.1` 而非 `0.0.0.0` 可减少网络暴露面

### 8.4 Shell 执行安全

1. 优先使用专用工具（`read_file`、`write_file`、`edit_file`）替代 shell 命令
2. 执行高危操作前确保已读取目标文件内容
3. 使用 `save_checkpoint` 创建检查点后再执行破坏性操作
4. 使用 `danger_override=true` 前必须验证操作的安全性

### 8.5 日志安全

1. 配置项 `logging.level` 控制日志详细程度，生产环境建议设为 `INFO`
2. 错误日志自动通过 `_monitor_log_errors` 定期检查（每 2 小时）
3. 日志不记录 API Key、密码等敏感信息
4. 日志文件轮转由操作系统层面管理
