# nanobot-mg 代码审查综合报告

**审查日期**：2026-07-06
**审查范围**：AgentLoop + AgentRunner + Tools + Network + Memory/Compress
**审查方法**：数据流分析 + 控制流分析 + 上下游依赖链 + 编译验证

---

## 一、编译验证

| 模块 | 结果 |
|------|------|
| `nanobot/agent/` 全部文件 | ✅ COMPILE_OK |
| `nanobot/providers/` 全部文件 | ✅ COMPILE_OK |
| `nanobot/agent/tools/shell_validators.py` | ✅ COMPILE_OK |
| `nanobot/security/network.py` | ✅ COMPILE_OK |
| `nanobot/agent/compress.py` | ✅ COMPILE_OK |
| `nanobot/agent/memory_extractor.py` | ✅ COMPILE_OK |

### SyntaxError（5 个文件）

| 文件 | 行 | 问题 | 严重度 |
|------|-----|------|--------|
| `dingtalk.py` | 500 | 悬空 `else:` 语法错误 | 🔴 真实 bug |
| `dingtalk.py` | 481-501 | 重试循环 `break` 无条件执行，except 分支永不触发 | 🔴 真实 bug |
| `docx/ooxml/scripts/pack.py` | 93 | `match` 语句（Python 3.10+，系统 Python 3.9） | ⚠️ 环境不兼容 |
| `docx/ooxml/scripts/validate.py` | 46 | `match` 语句（Python 3.10+） | ⚠️ 环境不兼容 |
| `pptx/ooxml/scripts/pack.py` | 93 | `match` 语句（Python 3.10+） | ⚠️ 环境不兼容 |
| `pptx/ooxml/scripts/validate.py` | 46 | `match` 语句（Python 3.10+） | ⚠️ 环境不兼容 |

> `match` 语句是 docx/pptx skill 文件，不影响核心功能。但项目 `requires-python = ">=3.10"`，建议在 pyproject.toml 中明确标记这些 skill 的 Python 版本要求。

---

## 二、真实 Bug（必须修复）

### 🔴 Bug 1 — dingtalk.py 重试循环失效

**位置**：`nanobot/proxy/channels/dingtalk.py` L481-501

**问题**：HTTP 4xx 和 API errcode 非 0 时，只 log warning 不抛异常，然后执行 `break` 退出循环，**except 分支永远没机会执行**。

```python
for attempt in range(3):
    try:
        resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(...)   # 只 log
        else:
            result = resp.json()
            errcode = result.get("errcode")
            if errcode not in (None, 0):
                logger.warning(...)  # 只 log
        break  # ← 无条件执行！重试逻辑完全失效
    except Exception as e:
        if attempt < 2:
            time.sleep(wait)
        else:
            logger.error(...)
    else:  # ← 悬空！SyntaxError
        logger.info(f"Sent {media_type}...")
```

**对比正确版本**：L448-468 的 `sampleImageMsg` 函数在成功时使用 `return` 退出，HTTP/API 错误时 fall through 由外层 except 捕获并重试——逻辑正确。

**后果**：sampleFile（文件上传）发送失败时不重试，直接报错。

### 🔴 Bug 2 — dingtalk.py 悬空 `else:`

**位置**：`dingtalk.py` L500

**问题**：`else:` 与 `for ... else:` 无关（for 有自己的 else），也不是 `if ... else` 的一部分（对应的 if 在 L498），是独立的悬空 else。

---

## 三、AgentLoop + AgentRunner 发现（数据流/控制流）

**报告**：`review_dataflow_controlflow.md`

### P1 🔴 — assess_me 回调超时路径分散

- **位置**：`runner.py:290-315`
- **现状**：有 180s 超时，TimeoutError 返回空 `AssessResult()`
- **问题**：`loop.py:1038-1051` 的 `debug_root_cause` 调用无独立超时；`_make_retry_assess_callback` 对 Session callback 未设置独立超时
- **建议**：在 `_run_assess_callback` 层统一处理所有 assess 链路的超时

### P2 🟡 — tool result 替换依赖 LLM 输出 `[tool_summary]` 标签

- **位置**：`loop.py:1460-1480`
- **问题**：`_tc_summary` 仅从 assistant 消息提取 `[tool_summary:call_id]` 标签。如果 LLM 忘记输出标签，tool result 全部走截断路径
- **建议**：截断时保留前 N 字符 + 告知用户"结果已截断"的元信息

### P3 🟡 — Proactive Compression 阈值附近可能触发频繁压缩循环

- **位置**：`runner.py:582-588` + `loop.py:196-200`
- **问题**：`compress_trigger_tokens = history_token_limit * 1.5`。token 在阈值附近波动时：压缩 → 减少 → 用户消息 → 超阈值 → 再压缩
- **建议**：压缩后设置冷却期（如下一轮 iteration 不再触发 proactive 压缩）

### P4 🟡 — instructions lambda 每轮重建

- **位置**：`loop.py:782-785`
- **问题**：`self.tools.get_instruction_map()` 每轮调用，工具数量多时可能成为性能瓶颈
- **建议**：仅当工具集变化时重建 instruction map

### P5 🟡 — 压缩后 `skip` 参数可能偏移

- **位置**：`loop.py:1433-1437`
- **问题**：`messages[:] = result` 替换后，`_append_turn_to_session` 的 `skip` 基于压缩前 `initial_messages` 计算，可能 off-by-one
- **建议**：验证 `skip` 参数在压缩后仍指向正确的分界线

### P6 🟡 — Subagent wait loop 使用轮询

- **位置**：`runner.py:389-415`
- **问题**：轮询间隔固定 2s，最坏情况 300 次轮询，期间主循环被阻塞
- **建议**：改用 `asyncio.Event` 或 `asyncio.Condition` 通知机制替代轮询

### P7 🟢 — 注释存在误导性描述

- **位置**：`runner.py:588` 注释
- **问题**：注释说 "REPLACE stale instructions"，但实际是列表整体替换而非逐元素替换

---

## 四、Tools + Network 发现（安全数据流）

**报告**：`review_tools_dataflow.md`

### W1 🔴 — DNS 解析失败时 SSRF 防护存在盲点

- **位置**：`network.py:157-159` (`targets_internal_address`)
- **问题**：`socket.gaierror: continue` 导致无法解析的 hostname 绕过 SSRF 检查。无法解析的内部 hostname 可能指向内网 IP
- **已修复**：`targets_internal_address()` 现在在 DNS 失败时返回 `(True, "DNS resolution failed")` — 阻止而非放行

### W2 🟡 — `targets_internal_address` 同步阻塞

- **位置**：`network.py`
- **问题**：同步函数内调用 `socket.getaddrinfo`，与 `validate_url_target` 的 async 设计不一致
- **建议**：评估是否需要改为 async

### W3 🟡 — Windows `%VAR%` 路径展开缺失

- **位置**：`shell_validators.py`
- **问题**：Windows 路径展开仅处理 `%VAR%`，不处理 `\\server\share` UNC 路径
- **建议**：补充 UNC 路径处理

### W4 🟡 — `validate_resolved_url` 同样 DNS 失败静默放行

- **位置**：`network.py:118-119`
- **问题**：与 W1 相同模式
- **建议**：统一处理 DNS 失败

---

## 五、Memory/Compress 发现（上下文管理数据流）

**报告**：`review_memory_dataflow.md`

### M1 🟡 — Compress 触发后 skip 参数不变但 messages 被替换

- **位置**：`runner.py:582-588`
- **问题**：Proactive compress 将 `messages[:] = result`（整体替换），但 `skip` 仍基于压缩前 `initial_messages` 计算
- **建议**：验证 skip 在压缩后仍指向正确的分界线

### M2 🟡 — MemoryExtractor Cron 三阶段处理无原子性保证

- **位置**：`memory_extractor.py`
- **问题**：Step 1 (process) → Step 2 (write findings) → Step 3 (cleanup) 三阶段，Step 2 失败时 Step 3 仍会执行，可能导致不一致状态
- **建议**：在 Step 3 开头验证 findings 文件是否存在

### M3 🟢 — MemoryExtractor findings 写入无幂等性保护

- **位置**：`memory_extractor.py`
- **问题**：重复触发时可能追加重复 findings
- **建议**：写入前检查是否已存在相同 findings

---

## 六、修复优先级汇总

| ID | 模块 | 问题 | 优先级 |
|----|------|------|--------|
| Bug1 | dingtalk.py | 重试循环 break 无条件执行 | P0 🔴 |
| Bug2 | dingtalk.py | 悬空 else: 语法错误 | P0 🔴 |
| W1 | network.py | DNS 失败 SSRF 盲点 | P1 🔴 (已修复) |
| P1 | runner.py | assess_me 超时路径分散 | P2 🟡 |
| P2 | loop.py | tool result 截断无提示 | P3 🟡 |
| P3 | runner.py | 压缩循环风险 | P3 🟡 |
| W3 | shell_validators | Windows UNC 路径 | P3 🟡 |
| W2 | network.py | 同步阻塞 | P4 🟢 |
| P4 | loop.py | lambda 每轮重建 | P4 🟢 |
| P5 | loop.py | skip 参数偏移风险 | P4 🟢 |
| P6 | runner.py | 轮询替代事件 | P4 🟢 |
| M2 | memory_extractor | 三阶段无原子性 | P4 🟢 |
| M3 | memory_extractor | findings 无幂等性 | P5 🟢 |
| P7 | runner.py | 注释误导性 | P5 🟢 |

---

## 七、数据流验证（关键链路）

| 链路 | 验证状态 |
|------|---------|
| user message → session → context → LLM | ✅ 通路清晰 |
| LLM tool_calls → execute_tools → results → loop | ✅ 循环正确 |
| compress_event → messages[:] → 上下文更新 | ⚠️ skip 偏移风险 |
| shell_validators → check_command_safety → return None/warning | ✅ 设计合理 |
| network.py SSRF → targets_internal_address → DNS 失败阻止 | ✅ W1 已修复 |
| MemoryExtractor process → findings → team_board | ⚠️ 幂等性待改进 |

---

## 八、上游依赖验证

| 模块 | 被调用方 | 调用链完整性 |
|------|---------|------------|
| `loop.py` | `AgentLoop.process()` | ✅ 清晰 |
| `runner.py` | `AgentRunner.run()` → `execute_tools()` | ✅ 清晰 |
| `shell.py` | `check_command_safety()` | ✅ 清晰 |
| `web.py` | `validate_url_target()` → `targets_internal_address()` | ✅ 清晰 |
| `compress.py` | `split_history_by_budget()` → `compress_turns()` | ✅ 清晰 |
| `memory_extractor.py` | `run()` cron pipeline | ✅ 清晰 |

---

## 九、已验证的设计亮点

1. **Shell validators 短路链**：danger_override → DANGEROUS_PATTERNS → allowlist → internal URL → workspace boundary → preflight，顺序执行，早期返回
2. **Lambda 刷新模式**：`instructions` 是 lambda，每轮重新计算，subagent 写入 team_board 后主 Agent 看到最新内容
3. **双重压缩路径**：Proactive（token 驱动）+ Reactive（LLM signal），共享 `compressed_messages + summary` 数据结构
4. **Tool Result 替换**：`[tool_summary:call_id]` 标签可覆盖工具原始结果，节省上下文空间
5. **分层 Hook**：`before_iteration` → `before_llm_call` → `before_execute_tools` → `after_iteration`
