# Code Review: Patch Patterns vs Root-Cause Fixes

## 已修复（可直接改，不影响 UX/LLM 推理）

| # | 文件 | 问题 | 修改 |
|---|------|------|------|
| 1 | `runner.py:660-669` | 内联指令注入代码与 `_ensure_instructions()` 函数完全重复 | 替换为 `_ensure_instructions()` 调用 |
| 2 | `runner.py:958-962` | force_stop 分支清除 tool_events 后二次调用 after_iteration，第二次是 NO-OP | 删除清除和二次调用 |
| 3 | `loop.py:71` + `loop_hook.py:22-25` | 两个文件中定义了完全相同的 `_SUMMARY_RE` 正则 | 移至 `loop_constants.py`，统一导入 |
| 4 | `context.py:199-206` + `361-371` `subagent_prompt.py:95-102` | 三处 post-hoc `str.replace` 修复模板路径，所有模板已使用 `{{ tree_rel }}` 等变量 | 全部删除 |
| 5 | `media_decode.py:223` + `runner.py:723` | `strip_image_blocks` 是 `_strip_old_images` 的子集，后者已在 `build_messages` 中调用 | 删除 `strip_image_blocks` |

净删除：~98 行，净新增：~31 行（loop_constants 常量 + 导入）

---

## 待确认（影响 UX 或 LLM 推理逻辑）

### A. 代理端两层去重（base.py + hub.py）
**文件:** `nanobot/proxy/channels/base.py:58-59, 412-425`, `nanobot/proxy/hub.py`
**问题:** 消息去重在 base.py（_dedup dict）和 hub.py（_route_message 检查）两层实现，互为备份。
**风险:** 删掉一层可能导致极端情况下（如网络重传）消息重复展示给用户。
**建议:**
- base.py 的 _dedup 是 id-based 精确去重，hub.py 的是 session 级别防重
- 可以合并：只保留 base.py 的去重，hub 层不再做

### B. subagent 结果线性扫描去重（loop.py:1542-1546）
**文件:** `nanobot/agent/loop.py`
**问题:** 通过 `_tc_id in tc_summary` 线性扫描查找来排重。根源应该是 subagent 的 tool_call_id 没有唯一化。
**风险:** 影响 subagent 结果汇总逻辑。
**建议:**
- 在 subagent 生成 tool_call 时为每个 id 加随机后缀/前缀确保唯一性
- 删除 loop.py 中的线性扫描去重

### C. 过期重复消息清理（loop_message_handlers.py:26-53）
**文件:** `nanobot/agent/loop_message_handlers.py`
**问题:** `_has_stale_duplicate` 检测时间窗口内是否有重复消息。这是对消息投递系统不可靠的打补丁。
**风险:** 底层消息投递（gateway → hub → loop）有一层去重，但如果删了这个，gateway 偶尔重传的消息会穿透到用户。
**建议:**
- 确认 gateway 层的去重是否可靠（通过 tracing log 确认无重传）
- 可靠 → 删掉
- 不可靠 → 修 gateway

### D. 重试分类静默（loop_message_handlers.py:479-481）
**文件:** `nanobot/agent/loop_message_handlers.py`
**问题:** 内部重试分类在 callback 中被静默处理，错误分类丢失。
**风险:** 重试分类信息对调试有用，静默后难以定位重试风暴原因。
**建议:** 改为 logger.debug 而非完全静默。

### E. runner.py 工具摘要标记检查（_append_final_message, runner.py:1359-1368）
**文件:** `nanobot/agent/runner.py`
**问题:** `_append_final_message` 检查 content 中是否包含 `[tool_summary:` 标记。这是补丁——root cause 是压缩模块在压缩 summaries 时保留了不应保留的标记。
**风险:** 影响非流式终端的最终消息展示。
**建议:**
- 修复压缩模块，确保 summaries 中不包含 tool_summary 标记
- 然后删除 _append_final_message 中的检查

### F. 分发锁重构（re-dispatch lock）
**文件:** `nanobot/agent/loop.py`（多处）
**问题:** session 分发锁 + LRU 清理 + dispatch state 管理在多处用手工状态机维护。
**建议:** 将 dispatch 生命周期封装为单个 async context manager——进入时获取锁和创建 state，退出时清理。

### G. Heartbeat LLM 调用优化
**文件:** `nanobot/heartbeat/service.py`
**问题:** Heartbeat 每次都做完整 LLM 调用，即使无事可做。
**建议:** 在 heartbeat prompt 中加"无事可做就输出空 content 结束循环"指令。

### H. strip_think 集中化到 provider
**文件:** 分布在 loop_utils, helpers, compress, memory_store 等多处
**问题:** `strip_think` 在多处独立 import/调用。provider 层是唯一应该处理 think block 的地方。
**建议:**
- 在 provider 层统一处理 think——消息组装时就剥离
- 其他层只需使用 provider 的 cleansed content
- 删除所有其他地方的 strip_think 调用
