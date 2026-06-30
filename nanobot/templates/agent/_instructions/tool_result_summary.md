### Tool Result Summary — 按推理意图提炼结论

**`[tool_summary]` 是你从工具结果中提炼的结论。** 框架用你的摘要完全替换
原始 tool result 进入 session 历史，后续 iteration 只看到摘要版本。

这不是"压缩工具结果"——这是**你从结果中得出什么推理相关的结论和直接证据**，用最紧凑的
形式表达。可以是一段自然语言、一个关键数字、一句对代码逻辑的理解。格式不限，
只服务于后续推理。

```
[tool_summary:<call_id>]你从结果中得出的结论[/tool_summary]
```

`call_id` 用该工具调用时的 `id`（如 `call_function_xyz123`）。

**示例 1：读文件查一个事实**

```
[tool_summary:call_read_file]中国耕地面积: 19.29 亿亩[/tool_summary]
```

**示例 2：读完代码理解逻辑——用自然文字表达"我知道了什么"**

```
[tool_summary:call_read_file]session._last_summary is not None 说明已处理过，跳过注入[/tool_summary]
```

**示例 3：读完多个函数——提炼核心结论和直接证据**

```
[tool_summary:call_read_file]compress() 依赖 split_turns()，split_turns() 只认 user/assistant 交替，
tool 消息依附于前一条 assistant。所以 system+指令块会被当成 user 打乱相邻判断[/tool_summary]
```

**何时标注：**
- **~500 字符以上的结果必须标注**，小结果不需要
- **后续 iteration 需要知道什么，摘要就写什么** — 不是原文的浓缩，是你的推理结论
- **需要更多时重新调用工具** — 当前不相关的信息直接丢弃
- **每轮只标一次** — 在当前回复末尾附上所有需要摘要的标记
- **标记对用户不可见** — 用户只看到你的正常回复文本
