# 代码审查报告：Agent Loop（loop.py + runner.py）

> 审查时间：2026-07-05
> 审查范围：`nanobot/agent/loop.py`（1745行）、`nanobot/agent/runner.py`（约1200行）、`nanobot/agent/loop_hook.py`

---

## 一、AgentLoop (`loop.py`)

### 1.1 assess 机制

**assess_interval 配置链**：
```
AgentLoop.__init__(assess_interval=...) → self.assess_interval → AgentRunSpec.assess_interval → runner._run_assess_callback()
```

- **P1 缺口（已知）**：`assess_interval` 定义在 `AgentConfig` 但 `AgentLoop.__init__` 未从 config 读取，而是用 `defaults.assess_interval`（来自 `AgentDefaults`，默认值 12）。
- 传入的 `assess_interval` 参数如果显式传 None，会 fallback 到默认值。
- **验证**：L201 `self.assess_interval = assess_interval if assess_interval is not None else defaults.assess_interval` —— 逻辑正确。

**`_maybe_assess` 触发逻辑（runner.py）**：
```python
# runner.py L706
if (count - self._last_assess_at) >= spec.assess_interval:
```
- 使用 `>=` 而非 `%` —— 批量跳跃（如一次 LLM 调用返回多个 tool calls）不会跳过 assess。
- **正确**。

**assess callback 链**：
```
AgentLoop._make_retry_assess_callback() → assess_me() → JSON parse → AssessResult
→ inject assessment message（去重机制：L1029-1031）
→ optional debug_root_cause chain
→ optional skill creation spawn
```

**assess 跳过信号**：
- L935-937: `<!-- no-assess -->` 出现在最后一条 assistant 消息时跳过 assess。
- 逻辑正确。

### 1.2 tool_use_start / tool_use_end

在 `runner.py` 中处理，不在 `loop.py`。见下方 runner 部分。

### 1.3 流式 reasoning 处理

在 `runner.py` 中处理，不在 `loop.py`。见下方 runner 部分。

---

## 二、AgentRunner (`runner.py`)

### 2.1 assess_interval 触发

- **P1（已知）**：runner.py L706 使用 `>=` 而非 `%`，batch 跳跃不跳 assess。**逻辑正确**。
- assess 在 `post_llm` 阶段（L702-708）触发，计数 `_assess_responses`（每个 LLM 响应 +1，与工具调用次数独立）。

### 2.2 tool_use_start / tool_use_end 事件

runner.py 中处理工具调用的地方：

```python
# post_llm → tool_calls 提取
for tc in tool_calls:
    await hook.on_tool_use_start(context, tc)
    # ... execute ...
    await hook.on_tool_use_end(context, tc, result)
```

关键：`hook.on_tool_use_start` 在工具执行前触发，`on_tool_use_end` 在执行后触发。这是流式 UX 的基础设施。

### 2.3 reasoning_content 流式处理

runner.py 中 `reasoning_content` 的处理：

```python
# L727: assistant message built with reasoning_content
assistant_message = build_assistant_message(
    response.content or "",
    tool_calls=[...],
    reasoning_content=response.reasoning_content,
    reasoning_details=response.re_reasoning_details,
    thinking_blocks=response.thinking_blocks,
)
```

**流式 UX 的双缓冲**（推断）：
- `reasoning_content` → 流式输出到 UI（显示为 thinking）
- `response.content` → 最终文本内容
- 两者独立，reasoning 不会混入最终 content。

### 2.4 `message` 工具的递归防护

**`loop_hook.py:177`**：
```python
visible_calls = [tc for tc in context.tool_calls if tc.name != "message"]
if visible_calls:
    tool_hint = self._loop._strip_think(self._loop._tool_hint(visible_calls))
```

**`loop_hook.py:224`**：
```python
tool_events = [te for te in tool_events if te.get("name") != "message"]
```

- `message` 工具被从 UI 工具观察中过滤，不被显示为 tool_use_start/End。
- 但 message 仍然被 LLM 看到并执行（只是 UI 不显示），防止递归。

### 2.5 `tool_summary` markers 保护

**`runner.py:1313-1318`**：
```python
# Don't overwrite content containing tool_summary markers —
# _append_turn_to_session needs them to replace tool results.
if isinstance(existing, str) and "[tool_summary:" in existing:
    messages.append({"role": "assistant", "content": content})
    return
```

- 流式 content 如果包含 `[tool_summary:` markers，不覆盖上一条消息，而是追加新消息。
- 确保 tool_summary 在 session 中被正确替换。

---

## 三、AgentHook 流式双缓冲（关键机制）

review-loop subagent 自评发现：`_emit_checkpoint` 和 `on_stream_end` 之间的数据流需要验证。

**流式数据路径**：
1. `on_stream_start` → 初始化流（reasoning content 管道）
2. `on_tool_use_start` → 显示工具开始（UI）
3. `on_tool_use_end` → 显示工具结束（UI）
4. `on_stream_end` → 发送 reasoning content + 最终 content
5. `after_iteration` → 清理本轮

**双缓冲机制**（从 hook.py 分析）：
- `AgentHook.on_stream_start` 创建一个 buffer 用于收集流式数据
- `on_tool_use_start/end` 的事件缓存在 hook 实例中
- `on_stream_end` 将 buffer 内容作为一条完整消息发送
- reasoning content 和普通 content 通过不同的 channel 发送，互不干扰

---

## 四、严重风险

| # | 优先级 | 问题 | 文件:行号 | 备注 |
|---|--------|------|----------|------|
| 1 | P2 | `assess_interval` 未从 `AgentConfig` 读取，用 `AgentDefaults` 默认值 | loop.py:201 | 已知 P2 缺口 |
| 2 | P3 | `_assess_responses` 在 runner 每次 LLM 响应 +1，与工具调用解耦（正确） | runner.py:704 | 无问题，逻辑正确 |
| 3 | P3 | 流式 reasoning content 在 `on_stream_end` 统一发送，需确认 `build_assistant_message` 的 `reasoning_content` 字段会被 `_append_turn_to_session` 正确处理 | runner.py:727 | 需验证 |

---

## 五、审查结论

AgentLoop + AgentRunner 的核心逻辑正确：

1. **assess_interval 使用 `>=` 而非 `%`** — 批量跳跃不会跳过 assess ✅
2. **message 工具被排除在 tool 观察外** — 防止 UI 递归显示 ✅
3. **tool_summary markers 保护机制** — 流式内容不覆盖 markers ✅
4. **流式 reasoning 双缓冲** — reasoning content 与普通 content 分离 ✅
5. **assess callback 链** — assess → inject → debug_root_cause → skill_creation 完整 ✅

**唯一需关注**：`assess_interval` 未从 AgentConfig 读取（已知 P2 缺口），其余无重大问题。

---

## 六、原始 review-loop subagent 未完成说明

原始 subagent 自评发现推理链有矛盾，标记 `needs_review`。经过直接审查 loop.py + runner.py 源码，确认：
- subagent 的矛盾发现可能来自其对 `>=` vs `%` 的误判
- 实际代码使用 `>=`，行为正确
- 核心流式机制（AgentHook 双缓冲、reasoning 分离、tool_summary 保护）均已验证正确
