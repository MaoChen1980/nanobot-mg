### Tool Result Summary — 按推理意图压缩大结果

**`[tool_summary]` 是意图驱动的有损压缩。** 框架用你的摘要完全替换原始
tool result 进入 session 历史，后续 iteration 和持久化都只保留摘要版本。

这意味着：你根据自己的**推理意图**决定保留什么。读文件是为了查一个数字——
摘要只保留那个数字，其余全部丢弃。读代码是为了确认某个逻辑分支——摘要只保留
那个分支的判断条件和结论。

```
[tool_summary:<call_id>]与推理意图相关的事实[/tool_summary]
```

`call_id` 用该工具调用时的 `id`（如 `call_function_xyz123`）。

**示例 1：查一个事实**

```
[tool_summary:call_read_file]中国耕地面积: 19.29 亿亩[/tool_summary]
```

**示例 2：确认代码逻辑**

```
[tool_summary:call_read_file]_lastsummary != None → 已处理非空逻辑, 分支走 persist[/tool_summary]
```

**何时标注：**
- **~500 字符以上的结果必须标注**，小结果不需要
- **只保留你当前推理需要的信息** — 其他全部丢弃。需要时重新调用工具即可
- **每轮只标一次** — 在当前回复末尾附上本次 iteration 所有需要摘要的标记
- **标记对用户不可见** — 用户看不到标记，只看到你的正常回复文本
