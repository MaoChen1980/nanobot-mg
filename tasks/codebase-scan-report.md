# 代码库扫描报告

扫描日期: 2026-05-16
扫描范围: 全库 Python 文件、SQLite schema、配置、模板

---

## 1. 数据库层问题

### 1.1 `facts` 表是死代码——只写不读

`db.py` 中有完整的 `upsert_fact()` / `list_facts()` 实现，但整个代码库中没有任何地方调用它们。无 `query_facts` 工具暴露给 LLM，context builder 也不读取 facts。

**影响**: 死功能，徒增 schema 复杂度。

**文件**: `nanobot/agent/db.py:72-83`, `:491-533`

---

### 1.2 `save_session()` 不是原子操作

`db.py:238-251` 先 `DELETE FROM messages WHERE session_key = ?`，再逐条 `INSERT`，最后 `commit()`。中途崩溃会丢失会话全部消息。

**影响**: 数据完整性风险。

**文件**: `nanobot/agent/db.py:238-251`

---

### 1.3 `tool_calls` 表无限制增长

每次工具调用插入一行，无保留策略/压缩机制。查询 `ORDER BY id DESC` 会随表增长变慢。

**文件**: `nanobot/agent/db.py:84-100`

---

### 1.4 `events` 表无保留策略

事件通过 `insert_event()` 累积，从不清理。context builder 中 hardcoded limit 为 5（`context.py:344`），掩蔽了增长问题但未解决。

**文件**: `nanobot/agent/db.py:59-71`、`nanobot/agent/context.py:344`

---

## 2. 会话管理问题

### 2.1 `last_consolidated` 是遗留死负载

`session/manager.py:35` 定义，但该字段在整个生命周期中**只递减、不递增**（`trim_old_turns` 中 `max(0, -trim_msg_count)`，`retain_recent_legal_suffix` 中 `max(0, -dropped)`）。初始化值始终为 0，所以 `get_history()` 中的切片 `self.messages[self.last_consolidated:]` 等效于 `self.messages[0:]`。

**影响**: 字段无实际作用，增加复杂度和混淆。

**文件**: `nanobot/session/manager.py:35`, `:254-255`

---

### 2.2 双重存储路径（DB vs 文件）逻辑重复

`SessionManager` 有完整的双实现：`save_session()` / `_save_to_file()`、`load_session()` / `_load_from_file()`、`read_session_file()` / `_read_session_file_from_file()`、`list_sessions()` / `_list_sessions_from_file()`。

DB 模式下文件代码不执行但保留在代码库中，从文件迁移到 DB 会丢失历史。

**文件**: `nanobot/session/manager.py:359-677`

---

### 2.3 会话缓存使用 `threading.Lock()` 而非 `asyncio.Lock()`

`_cache_lock` 在线程锁保护下读写 `_cache` dict。`get_or_create()` 持有锁时可能阻塞事件循环（如果其他协程也在等待锁）。

**文件**: `nanobot/session/manager.py:281`, `:302-313`

---

## 3. Context Builder 问题

### 3.1 Hardcoded 限制——无配置覆盖

```
context.py:48   _MAX_RECENT_HISTORY = 10    # DB 历史摘要上限
context.py:49   _MAX_HISTORY_CHARS = 64_000  # 历史部分字符限制
```

两者均无法通过 config.json 覆盖。

---

### 3.2 会话摘要 `session_summary` 始终为 None

`message_handlers.py:208-213`:
```python
cs = ContextState(
    session_summary=pending,  # pending 永远为 None
    ...
)
```

上下文中的 `[Resumed Session]` 块始终为空。来自旧系统的残留。

**文件**: `nanobot/agent/loop_message_handlers.py:208-213`

---

### 3.3 模块级缓存 `_template_content_cache` 内存泄漏

`context.py:25` 的全局 `dict[str, tuple[float, str]]` 只增不减。每个新模板文件永久驻留内存。

---

## 4. 记忆提取问题

### 4.1 `_pt_save_interval` 硬编码 100，配置默认 30——配置被忽略 🔴

```python
# loop.py:237
self._pt_save_interval = 100  # default M, overridden via config

# config/schema.py:143
save_interval: int = Field(default=30, ge=1)
```

**无代码将配置值传递到循环**。loop 构造函数不接收 `pt_save_interval` 参数；`nanobot.py` 的 `from_config()` 也不传递。结果：用户设 `save_interval=5` 但实际行为仍是 100。

**影响**: 提取器看到的 .pt 文件减少约 70%，低频存档导致大量上下文在 crash 时丢失。

**文件**: `nanobot/agent/loop.py:237`、`nanobot/config/schema.py:143`

---

### 4.2 `/new` 不重置 `_pt_counters`

`cmd_new` 调用 `session.clear()` + `invalidate()`，但不清零 `loop._pt_counters[session_key]`。新会话继承旧计数器，.pt 保存间隔偏移。

**文件**: `nanobot/command/builtin.py:94-118`、`nanobot/agent/loop.py:236`

---

### 4.3 FAISS 重建总是完全重建

`memory_vector.py:129-161` 每次 `build_from_files()` 重新创建整个索引，即使只追加一个文件。无增量更新支持。

---

## 5. 命令系统问题

### 5.1 `/status` 注册了两次 🔴

```python
# builtin.py:441
router.priority("/status", cmd_status)
# builtin.py:448
router.exact("/status", cmd_status)
```

已在 priority 路由中注册，又在 exact 路由中重复注册。取决于 router 实现可能静默失败。

**文件**: `nanobot/command/builtin.py:441`, `:448`

---

### 5.2 `cmd_sub` / `_format_subagent_status` 重复定义 🔴

第 152 行和第 191 行定义了两个完全相同的 `_format_subagent_status` 函数，第 173 行和第 183 行定义了两个 `cmd_sub`。第二个定义静默覆盖第一个（Python 允许）。

**影响**: 函数体相同因此当前无 bug，但任何对前半部分的编辑都会丢失。

**文件**: `nanobot/command/builtin.py:152-170`, `:191-209`

---

### 5.3 `/new`、`/clear`、`/reset` 行为 100% 相同

所有三个调用 `cmd_new`：取消任务 → 存档消息 → 清除会话 → 失效缓存。但语义不同：
- `/clear` 应只清除（不存档）——类似清聊天界面
- `/reset` 应清除 + 重置元数据（如 `max_keep_rounds`）
- `/new` 正确存档并清除

**文件**: `nanobot/command/builtin.py:94-118`, `:446-447`

---

### 5.4 无 `/sessions` 命令

会话管理仅通过 HTTP API 可见，CLI 用户无法列出/删除会话。

---

## 6. 跨领域问题

### 6.1 TaskExecutor 集成不完整

`loop.py:193-202` 创建，仅由 `/goal` 和 `/resume_goal` 使用。不与主消息流集成，不自动处理目标执行。

**文件**: `nanobot/agent/loop.py:193-202`、`nanobot/command/builtin.py:212-271`

---

### 6.2 存档的双重路径（history 表 + .pt 文件）

- `/new` 时 → `archive_session()` → `history` 表（原始摘要）
- 提取器读取来自 `history` 表 + `.pt` 文件
- `.pt` 文件通过计数器机制（每 M 轮）保存
- session 生命周期早期存档的历史在 `pt_save_interval` 触发前不会被提取器处理

---

### 6.3 SQLite 连接无连接池/重试

单个 `sqlite3.connect`，`timeout=30`。`save_session()` 的 `DELETE+INSERT` 在繁忙负载下可能冲突。

**文件**: `nanobot/agent/db.py:29`

---

## 优先级汇总

| 优先级 | 问题 | 文件 |
|--------|------|------|
| **P0** | `_pt_save_interval` 硬编码 100，配置 30——配置被忽略 | `loop.py:237` |
| **P0** | `/status` 注册了两次 | `builtin.py:441,448` |
| **P0** | `cmd_sub` / `_format_subagent_status` 重复定义 | `builtin.py:152,191` |
| **P1** | `save_session()` 非原子操作（DELETE+INSERT 无事务） | `db.py:238-251` |
| **P1** | `tool_calls` 表无保留策略 | `db.py:84-100` |
| **P1** | `facts` 表完全未连接——死代码 | `db.py:72-83,491-533` |
| **P1** | `/new` 不重置 `_pt_counters` | `loop.py:236`, `builtin.py:94-118` |
| **P2** | `last_consolidated` 是遗留死负载 | `session/manager.py:35` |
| **P2** | `events` 表无保留策略 | `db.py:59-71` |
| **P2** | `/new/clear/reset` 语义相同但意图不同 | `builtin.py:94-118` |
| **P2** | 会话摘要 `session_summary` 始终为 None | `message_handlers.py:208` |
| **P3** | `_template_content_cache` 模块级缓存泄漏 | `context.py:25` |
| **P3** | 无 `/sessions` 命令 | 缺失功能 |
