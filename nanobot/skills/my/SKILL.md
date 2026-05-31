---
name: my
description: Inspects and modifies agent runtime state — model, iteration limit, context window, and settings. Operates in-memory only; changes reset on restart. Use when diagnosing issues, checking capabilities, or adjusting configuration.
version: 0.1.0
---

# Self-Awareness, tools from system

## How to use

1. **识别场景** 从下方类别中找出对应场景
2. **调用 my 工具** 传入合适的 action
3. **如果是设置操作**，在更改有影响的配置前警告用户（model, iterations）
4. **查看详细示例**，阅读 `{baseDir}/references/examples.md`

## When to check

<rule>
**先诊断再解释。** 遇到问题时，先检查你的状态。
</rule>

<rule>
**复杂任务前检查预算。** 在承诺前了解你的限制。
</rule>

<rule>
**跨轮次记忆。** 将偏好存储在 scratchpad 中，稍后读回。
</rule>

## When to set

<rule>
**仅在收益明确且用户知情时设置。** 更改 model 前发出警告。
</rule>

| 场景 | 命令 |
|-----------|---------|
| 大型代码库分析 | `my(action="set", key="context_window_tokens", value=131072)` |
| 重复性简单任务 | `my(action="set", key="model", value="<fast-model>")` |
| 长流程多步骤任务 | `my(action="set", key="max_iterations", value=80)` |
| 启用 thinking 模式（Anthropic/MiniMax） | `my(action="set", key="reasoning_effort", value="max")` |

**权衡：** 倾向于稳定。仅在默认值确实不足时设置。

## Anti-patterns

<rule>
**不要每轮都检查。** 消耗一次 tool call。在需要信息时使用，不要成为习惯性操作。
</rule>

<rule>
**不要存储敏感数据。** scratchpad 中不要存放 API 密钥、密码或令牌。
</rule>

<rule>
**不要设置 workspace。** 不会更新 file tool 边界——无效。
</rule>

## Constraints

- 所有修改仅在内存中——重启后全部重置
- 受保护参数有类型/范围校验：`max_iterations` (1–100)，`context_window_tokens` (4096–1M)，`model`（非空字符串）
- 如果 `tools.my.allow_set` 为 false，则只能检查

## Related tools

| 需求 | 使用 | 持久？ |
|------|-----|-----------|
| 单次 session 临时状态 | `my(action="set", key="...", value=...)` | 否 |
| 长期事实 | Memory skill（`MEMORY.md`、`USER.md`） | 是 |
| 永久配置更改 | 编辑配置文件 | 是 |

**经验法则：** 明天还用？用 Memory。仅本轮？用 My。

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
