# 代码审查报告：Tools & Shell 执行层

**审查范围**：`shell.py`、`base.py`（Tool 基类 + Schema）、`registry.py`、`shell_validators.py`、`danger.py`、`sandbox.py`、`filesystem_base.py`、`output_cache.py`、`file_state.py`
**审查时间**：2026-07-05
**审查者**：nanobot Orchestrator Subagent（代码审查专家角色）

---

## 一、安全（Security）

### P1：`_guard_command` 警告不阻断执行，可被静默绕过

**文件**：`shell.py` L113–122（execute 方法入口）

```python
suggestion = ""
try:
    # Guard: detect dangerous commands and warn
    if (guard := self._guard_command(command, cwd, danger_override)) is not None:
        suggestion = guard + "\n"
    if from_cache:
        return await self._from_cache_mode(...)
```

**问题**：当 `danger_override=False` 且 `_guard_command` 返回警告时，警告被存入 `suggestion` 变量，**但执行流程直接继续**，没有任何分支跳转。`suggestion` 只是被 prepend 到结果字符串末尾。

**后果**：
- `danger_override=True` → guard 返回 `None` → 跳过警告 → 执行继续（符合设计意图）
- `danger_override=False` + 检测到危险命令 → guard 返回 `⚠️ Danger: ...` → **警告被记录但命令仍然执行** → LLM 看到警告的附加在结果末尾

**实际利用路径**：若 LLM 调用 `exec(command="rm -rf /", danger_override=True)`，guard 短路返回 `None`（L123 `if danger_override: return None`），命令直接执行。危险操作不需要猜，调用参数即可绕过。

**另一个问题**：即使 `danger_override=False`，危险命令也会执行——这是设计缺陷而非安全漏洞，但不符合"警告即阻断"的预期语义。

**修复建议**：
```python
# 方案 A：危险命令无条件阻断（推荐）
guard_result = self._guard_command(command, cwd, danger_override)
if guard_result is not None and not danger_override:
    return guard_result  # 阻断，警告即是返回

# 方案 B：显式 danger_override 才跳过检查
if danger_override:
    suggestion = "⚠️ Proceeding despite safety warning.\n"
elif (guard := self._guard_command(...)) is not None:
    return guard  # 阻断
```

**影响范围**：所有通过 `exec` 工具执行的 shell 命令。

---

### P2：`COMSPEC` 环境变量指向 `powershell.exe` 可能绕过 `cmd.exe` 隔离

**文件**：`shell.py` L825–851（`_build_env`）

```python
"COMSPEC": os.environ.get("COMSPEC", f"{sr}\\System32\\cmd.exe"),
```

`_spawn` 固定使用 `cmd.exe /c`（L755），但 `COMSPEC` 设为 `powershell.exe` 会影响被启动进程的默认 shell 环境。若子进程内部使用 `%COMSPEC%` 启动孙子进程，可能绕过预期的命令执行隔离。

当前实际风险低（`_spawn` 直接用 `cmd.exe`），但语义上 `COMSPEC` 应该保持 `cmd.exe`。

**修复建议**：移除 `COMSPEC` 注入，或设为 `cmd.exe`。

---

### P3：危险模式检测中 `lower = command.strip().lower()` 会丢失大小写区分

**文件**：`shell_validators.py` L40

```python
lower = command.strip().lower()
for pattern in deny_patterns:
    if re.search(pattern, lower):
```

`lower()` 使正则中的 `\brm\b` 变为 `\brm\b`（无大小写），但某些系统命令存在大小写变体（如 `DEL`、`Format`）。这不构成直接漏洞，但可能导致大写危险命令未被检测。

---

## 二、正确性（Correctness）

### P1：输出截断按字节而非字符，可能切割 UTF-8 多字节字符

**文件**：`shell.py` L381–382

```python
if len(_stdout_bytes) > self._MAX_OUTPUT:
    stdout_text = _stdout_bytes[-self._MAX_OUTPUT:].decode("utf-8", errors="replace")
```

`len(_stdout_bytes)` 是字节数，`_MAX_OUTPUT` 是字符数（如 8000）。若截断点在 UTF-8 字符中间（如中文 3 字节），`decode("utf-8", errors="replace")` 会产生 U+FFFD 替换字符，导致输出损坏。

**修复建议**：
```python
stdout_text = stdout_full  # 先完整解码
char_count = len(stdout_text)
if char_count > self._MAX_OUTPUT:
    stdout_text = stdout_text[-self._MAX_OUTPUT:]
```

stderr 截断（L387–394）有相同问题，且还加了前缀文本，处理更复杂（应先 decode 再截断字符）。

---

### P2：进程终止竞态 — `_kill_process` 后无等待保证

**文件**：`shell.py` L778–811

```python
await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
# ... 继续执行，process 可能尚未终止
await asyncio.wait_for(process.wait(), timeout=5.0)
```

`taskkill /T /F` 是异步的（发送终止信号），3s 超时后若 `process.wait()` 仍阻塞，`finally` 块继续执行清理逻辑，但进程可能仍是僵尸状态。Linux 分支有 `WNOHANG` 处理，但 Windows 无等价机制。

**修复建议**：在 `taskkill` 后显式等待 `process` 终止：
```python
kill_proc = await asyncio.create_subprocess_exec("taskkill", "/T", "/F", f"/PID", str(process.pid), ...)
try:
    await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
except (OSError, asyncio.TimeoutError):
    pass
# 确保 process 已终止
try:
    await asyncio.wait_for(process.wait(), timeout=5.0)
except asyncio.TimeoutError:
    logger.warning("Process {} did not terminate after taskkill", process.pid)
```

---

### P3：`dedup` 与 `cache` 交叉影响 — 第二次相同 read_only 调用返回 dedup 消息而非缓存结果

**文件**：`registry.py` L144–149 + L188–190

```python
# L144–149: cache hit → return cached result
if tool.read_only:
    cached = self._cache.get(name, params)
    if cached is not None:
        cached_result, age = cached
        return self._format_result(name, cached_result) + f"\n(cached {age}s ago)"

# L188–190: dedup check AFTER cache（此时第一次的结果已在 result_str）
if self._cache.check_duplicate(result_str):
    return "[Content unchanged since previous tool call — see earlier output for the full content.]"
```

逻辑分析：
- 第 1 次调用 read_only 工具：缓存未命中 → 执行 → `put()` 缓存 → `check_duplicate()` 检查（首次 → 不重复）→ 返回结果
- 第 2 次调用（相同参数）：缓存命中 → 返回 `result + "\n(cached Xs ago)"` → **dedup 不触发**（因为缓存分支提前 return）
- 第 2 次调用（不同调用者不同结果但内容相同）：缓存未命中 → 执行 → dedup 触发 → 返回 dedup 消息

实际行为：**对 read_only 工具的缓存命中不会触发 dedup**，这基本合理。但 dedup 的 20 条历史与 cache TTL 完全独立，若长时间运行后相同结果来自不同工具调用，dedup 消息可能让 LLM 困惑（因为 LLM 无法看到"上一次"的实际内容）。

**建议**：在 dedup 消息触发时，同时提供"结果与第 N 次调用相同"的时间信息。

---

## 三、健壮性（Robustness）

### P2：`tool_parameters` 装饰器中 `required` 列表去重缺失

**文件**：`base.py` L396

```python
required = schema_copy.get("required", [])
# 遍历 required 中的每个 key
for key in required:
    prop = props.get(key, {})
    if isinstance(prop, dict) and prop.get("type") == "string" and "minLength" not in prop:
        prop["minLength"] = 1
```

若 `schema["required"]` 包含重复字段名（如 `["path", "path", "mode"]`），循环会对同一 `prop` 字典多次修改（无额外影响），但不会报错。若后续 schema 校验逻辑依赖 `required` 列表无重复假设，可能产生边界问题。

**影响**：低。装饰器通常由开发者手动编写，`required` 重复是使用错误而非系统问题。

---

### P2：Python 文件语法检查失败时 fallback 到 `pyright` 但不验证 pyright 存在性

**文件**：`filesystem_base.py` L107–118

```python
except py_compile.PyCompileError as e:
    try:
        pyright_result = subprocess.run(
            ["pyright", "--", str(fp)],
            capture_output=True, text=True, timeout=30,
        )
        if pyright_result.returncode != 0:
            return f"Syntax check failed:\n{errors or str(e)}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # ← pyright 不存在时静默忽略
    return f"Syntax check failed:\n{e}"
```

若 `pyright` 不可用，`FileNotFoundError` 被 `pass`，然后返回原始 `py_compile` 错误（而非告知用户 pyright 也不可用）。这意味着 Windows 等未安装 pyright 的环境无法获得增强的错误信息。

**修复建议**：在 `except FileNotFoundError` 中返回一条明确消息：
```python
except FileNotFoundError:
    return f"Syntax check failed (pyright not installed):\n{e}"
```

---

### P2：`_extract_absolute_paths` 正则 `\~` 字符类缺失反斜杠转义

**文件**：`shell.py` L889

```python
home_paths = re.findall(r"(?:^|[\s|>'\"`])(~[^\s\"'>;|<]*)", command)
```

正则中的 `~` 在字符类 `[]` 内是字面量，但若命令中出现 `~foo`（无空格前缀），正则要求 `~` 前必须是 `[\s|>'\"`]` 中的字符之一，因此 `~\file` 会匹配，但 `~file`（tilde 后紧跟字母）不会匹配。这可能是预期行为（避免误匹配 `~foo` 变量），但与注释描述不符。

**实际影响**：低。`~` 通常有空格分隔或位于命令开头。

---

### P3：`_spawn` 中 `login=True` 在显式提供 `shell_program` 时仍然添加 `-l`

**文件**：`shell.py` L763–766

```python
shell_program = shell_program or shutil.which("bash") or "/bin/bash"
args = [shell_program]
if login:
    args.append("-l")
args.extend(["-c", command])
```

当显式传入 `shell_program`（如 `/bin/sh`）时，`login=True` 仍会追加 `-l`。login shell 会 source `/etc/profile` 和 `~/.bash_profile`，这可能改变执行环境。若明确指定了 `shell_program`，通常不希望触发 login shell 行为。

**建议**：文档中明确说明，或在显式指定 `shell_program` 时忽略 `login` 参数。

---

## 四、设计与可维护性

### P2：`_resolve_path` 抛出原始 `ValueError`/`PermissionError`，消息未格式化

**文件**：`filesystem_base.py` L30–32

```python
if not p.is_absolute():
    raise ValueError(f"Path must be absolute, got: {path}.{hint}")
```

这些异常在工具层被 `except Exception as e` 捕获，变为：
```
Error executing read_file: Path must be absolute, got: ...
```

**影响**：LLM 看到 "Error executing read_file" 而非更精确的"路径必须是绝对路径"，需要额外的推理才能理解错误原因。

**建议**：在 `filesystem_read.py` 等工具的 `execute()` 中捕获特定异常，提供用户友好的错误消息。

---

### P3：`OutputCache` 的 dedup 历史不过期 — 与 cache TTL 行为不对称

**文件**：`output_cache.py` L19–23

```python
def __init__(self, ttl: int = 60, max_entries: int = 100, max_history: int = 20):
    self._ttl = ttl           # 缓存条目过期时间
    self._max_history = 20   # dedup 历史固定 20 条，无过期机制
```

缓存条目有 TTL（60s 过期），但 dedup 指纹历史（固定 20 条）永不过期。若相同内容在 60s 前触发过 dedup，其指纹仍在 `_fingerprints` 列表中，下次完全不同的工具返回相同文本也会被 dedup。

**影响**：低。仅当不同工具恰好返回相同文本时才触发。

---

### P3：`file_state.py` 中模块级 `_default_manager` 与 `FileStateManager` 实例并存

**文件**：`file_state.py` L181–224

文件同时导出：
1. `FileStateManager` 类（支持会话隔离，通过 contextvars）
2. 模块级 `_default_manager` 实例 + 兼容函数（无会话隔离，`session_key=None`）

测试使用模块级函数（`record_read()`），生产使用 `FileStateManager`。两套并行增加维护负担。

**建议**：在生产代码中统一使用 `FileStateManager`，测试代码通过依赖注入获得实例。

---

## 五、正向发现（值得保留的设计）

以下设计决策是良好的，在本次审查中确认：

1. **shell.py `_guard_command` 返回警告而非抛异常**：允许 LLM 在确认风险后重试，这是正确的设计，问题在于警告不阻断（见 P1）。

2. **`danger_override` 参数设计**：明确区分"警告后可绕过"（`danger_override=True`）和"无条件阻断"两种场景，语义清晰。

3. **`FileStateManager` 使用 contextvars 做会话隔离**：通过 `_current_session_key` contextvar 确保不同 agent session 的文件状态互不干扰，设计优雅。

4. **`shell_validators.py` 中正则使用命令边界 `\b`**：危险模式如 `\brm\s+-rf\b` 使用词边界避免误匹配文件名中的字符串，设计合理。

5. **`output_cache.py` 中 cache 和 dedup 是正交机制**：缓存避免重复执行，dedup 避免重复返回，设计分离清晰。

6. **sandbox.py 中 bubblewrap 的 tmpfs 覆盖 config 目录**：正确隐藏配置文件，设计周全。

---

## 六、测试覆盖缺口

基于代码分析，以下边界条件缺少单元测试：

| 场景 | 文件 | 说明 |
|------|------|------|
| UTF-8 多字节字符截断边界 | `shell.py` L381 | 当前截断按字节，应测试中文字符 |
| `danger_override=True` 绕过 guard | `shell.py` | 需要端到端测试验证 |
| `_guard_command` 返回警告后命令仍执行 | `shell.py` | 确认 P1 行为 |
| dedup 在非 read_only 工具上的行为 | `registry.py` L189 | 不同工具相同结果应触发 dedup |
| `_kill_process` 超时场景 | `shell.py` | 进程拒绝终止时的行为 |
| `FileStateManager` 会话隔离 | `file_state.py` | contextvars 正确隔离多 session |

---

## 七、修复优先级汇总

| 优先级 | 编号 | 问题 | 文件 |
|--------|------|------|------|
| P1 | S1 | 危险命令警告不阻断执行 | `shell.py` L113-122 |
| P1 | C1 | UTF-8 字节截断切割多字节字符 | `shell.py` L381-382 |
| P2 | C2 | 进程终止竞态（Windows） | `shell.py` L778-811 |
| P2 | C3 | dedup 与 cache 交互不透明 | `registry.py` L144-190 |
| P2 | R1 | pyright 不可用时静默 fallback | `filesystem_base.py` L114 |
| P3 | R2 | 模块级 `_default_manager` 与类并存 | `file_state.py` |
| P3 | D1 | dedup 历史不过期 | `output_cache.py` |
| P3 | D2 | `_spawn` login 参数语义不清 | `shell.py` L763 |

**总计**：P1×2，P2×4，P3×4
