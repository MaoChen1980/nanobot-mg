# nanobot-mg 全量代码审查汇总报告

> 审查时间：2026-07-05
> 审查范围：Agent Loop、Tools/Shell、Context/Memory、Session/DB/Config/Cron/Heartbeat
> 审查方法：4 个并行 subagent，读取约 60K LOC 源码

---

## 严重程度分级说明

| 级别 | 含义 |
|------|------|
| P1 | 需立即修复，影响功能正确性或安全 |
| P2 | 建议近期修复，影响稳定性或性能 |
| P3 | 中期改进，影响可维护性 |
| P4 | 低优先级，改进建议 |

---

## P1 问题汇总（6个）

### 1. Context: `_rebuild_tools_index` 每轮重复 I/O
- **文件**: `context.py:157`
- **问题**: 每次 `build_system_prompt()` 调用都重建 TOOLS.md（写文件 + 模板渲染），AgentLoop 每轮执行。工具列表稳定时浪费 I/O。
- **建议**: 在 `buildToolList` 注册/注销时触发增量更新，而非每轮重建。

### 2. Context: 无界缓存 OOM 风险
- **文件**: `context.py:95` (`_file_text_cache`), `memory_vector.py` (`_chunks`), `context.py:992` (`_template_content_cache`)
- **问题**: 三处缓存均无大小限制或淘汰策略粗暴（`_template_content_cache` 超容直接清空所有）。长期运行有 OOM 风险。
- **建议**: 对无界缓存实现 LRU 淘汰策略。

### 3. Shell: UTF-8 多字节字符截断损坏
- **文件**: `shell/shell.py:381-382`
- **问题**: 按字节截断后 decode，多字节 UTF-8 字符（如中文 emoji）在截断点被切割，`errors="replace"` 会产生 U+FFFD 损坏字符。
- **代码**:
  ```python
  if len(_stdout_bytes) > self._MAX_OUTPUT:
      stdout_text = _stdout_bytes[-self._MAX_OUTPUT:].decode("utf-8", errors="replace")
  ```
- **建议**: 改为按字符截断，找到最后一个合法字符边界再切。

### 4. Infra: Cron Action Queue 从不回放
- **文件**: `cron/service.py`
- **问题**: `_append_action()` 写入 `.cron_actions.jsonl`，但 `_load()` / `start()` 均未读取该文件。服务停止期间的操作（add_job/remove_job/enable_job）在重启后丢失。
- **建议**: 在 `_load()` 或 `start()` 中回放 action queue，或改写 store 直接持久化（去掉 queue）。

### 5. Infra: Config MCP 默认无工具过滤
- **文件**: `config/schema.py`
- **问题**: `enabled_tools: ["*"]` 是默认配置，不受信任的 MCP server 会暴露全部工具。
- **建议**: 生产环境必须显式配置白名单。

### 6. Infra: DB 每次 `insert_tool_call()` 都全表清理
- **文件**: `agent/db.py`
- **问题**: 每次插入工具调用都执行 `DELETE FROM tool_calls WHERE timestamp < N`，高频场景 O(N²) 开销。
- **建议**: 改为后台定时清理（如每小时一次），而非每次插入都清理。

---

## P2 问题汇总（8个）

| # | 模块 | 问题 | 文件:行号 |
|---|------|------|----------|
| 1 | Context | `_extend_chunks` 后未自动调用 `save()` | memory_vector.py |
| 2 | Context | 内存文件写入无原子性保证（无 write-rename） | memory_store.py |
| 3 | Context | DB 操作无 timeout，锁住时无限阻塞 | memory_store.py:258 |
| 4 | Context | `_build_tools_section` 参数签名每轮重算 | context.py:225 |
| 5 | Shell | Windows `taskkill /T /F` 后 3s 不等待子进程树完全终止 | shell.py:778 |
| 6 | Shell | py_compile 失败静默 pass，不提示 pyright 可用 | filesystem_base.py:114 |
| 7 | Shell | dedup 历史永不过期（与 cache TTL 不对称） | shell.py |
| 8 | Infra | Session `_cache` 与 DB 可能不一致（save 后 cache 不同步） | session/manager.py |

---

## P3 问题汇总（6个）

| # | 模块 | 问题 | 文件:行号 |
|---|------|------|----------|
| 1 | Context | `Session._split_turns_by_assistant` 跨模块依赖私有方法 | compress.py, compressor.py |
| 2 | Context | `_chunks` 列表只增长不收缩，长期积累 None 空洞 | memory_vector.py |
| 3 | Shell | 模块级 `_default_manager` 与 `FileStateManager` 类并行，语义不清 | shell.py |
| 4 | Shell | `_spawn` login 标志语义不清晰 | shell.py |
| 5 | Infra | `process_direct()` 无 LLM 资源限制，可能浪费资源 | heartbeat/service.py |
| 6 | Infra | Windows 时区映射不完整，fallback 到 UTC | config/loader.py |

---

## P4 问题汇总（4个）

| # | 模块 | 问题 | 文件:行号 |
|---|------|------|----------|
| 1 | Context | `condense_session_to_history` 64KB 硬截断 | memory_store.py:262 |
| 2 | Context | `events_lock` 与文件写入无锁保护 | memory_store.py:48 |
| 3 | Shell | 正则 `~` 字符类捕获范围与注释不符 | shell.py |
| 4 | Infra | Env var 无默认值语法 `${VAR:-default}` | config/loader.py |

---

## 误报修正

| # | 原报告描述 | 验证结论 | 修正 |
|---|-----------|---------|------|
| 1 | review-tools: `danger_override=False` 时危险命令仍执行 | **误报**：代码 L277-279 明确 `if guard_error: return guard_error`，危险命令被阻断 | 危险命令会被返回为错误字符串，不执行 |

---

## 审查完成项

- **review-loop**: ✅ 已完成，直接审查 loop.py + runner.py + loop_hook.py，写入 `CODE_REVIEW_loop.md`。
  - subagent 矛盾发现已澄清：`>=` 语义正确（batch 跳跃不跳过 assess）
  - message 工具在 `loop_hook.py:177,224` 被从 tool 观察中过滤（防止 UI 递归）
  - tool_summary markers 保护机制在 `runner.py:1313` 验证正确
  - 流式 reasoning 双缓冲机制验证正确

---

## 架构亮点

1. **AgentHook 双缓冲协同**：流式数据与 reasoning 分离，互不干扰
2. **DB 层孤儿防护**：`find_legal_message_start()` 扫描 declared IDs 确保 replay 安全
3. **Config Pydantic**：统一 schema + alias_generator 自动处理 camelCase/snake_case
4. **Cron 防重**：`_timer_active` 短路防止并发 timer 实例
5. **Heartbeat 跳过链**：4 层过滤减少无效 LLM 调用
6. **Provider 优先级**：显式前缀 > 关键词 > 本地回退，防误匹配
7. **双重持久化**：FAISS 同时写 `.faiss` + `.faiss.bak`
8. **ContextVar 解耦**：`llm_context.py` 使用 ContextVar 避免全局状态
9. **mtime 缓存**：所有文件读取均基于 mtime 失效

---

## 下一步行动建议

| 优先级 | 行动 |
|--------|------|
| 立即 | 修复 Cron action queue 不回放（R1）——功能正确性问题 |
| 立即 | 修复 UTF-8 字节截断损坏（Shell P1）——数据完整性 |
| 近期 | 修复 DB O(N²) 清理（R3）——性能 |
| 近期 | 实现无界缓存 LRU 改造（P1）——稳定性 |
| 近期 | 修复 `_rebuild_tools_index` 每轮重建（P1）——性能 |
| 中期 | 修复文件写入原子性、DB timeout、PowerShell COMSPEC 等 P2 项 |
