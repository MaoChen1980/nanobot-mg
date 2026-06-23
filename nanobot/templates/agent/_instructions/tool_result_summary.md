### Tool Result Summary — 大结果必须提供摘要，替换原始结果

**`[tool_summary]` 不是压缩手段——摘要是后续 iteration 能看到的全部信息。**

当你处理完一个大工具结果后，用 `[tool_summary:call_id]...[/tool_summary]` 为它写摘要。
框架**用你的摘要完全替换原始 tool result** 进入 session 历史。后续 iteration 和跨 session
持久化都只保留摘要版本。原始结果不再存在。

这意味着：**摘要就是你为后续推理保留的全部信息。** 写得太短会丢失推理所需的关键事实。

```
[tool_summary:<call_id>]精简但保留关键事实的摘要[/tool_summary]
```

`call_id` 用该工具调用时的 `id`（如 `call_function_xyz123`）。例如：

```
我读完了 loop.py。关键发现：
- 核心方法：run (第 1647 行)，schedule/process/iterate
- 架构：异步 + 钩子 + 子代理

[tool_summary:call_function_xyz123]loop.py 核心方法: run (L1647), schedule/process/iterate, 异步+钩子+子代理架构, 主入口 run() 管理全生命周期[/tool_summary]
```

**写摘要的标准：**
- **保留关键事实** — 数字、路径、名称、关系、结论，一样不能少
- **结构完整** — 条理化呈现，让后续 LLM 能直接利用
- **不确定是否重要时，宁可多写** — 丢了就再也回不来，原始结果已被替换
- **不是给你的回复写摘要** — 是给 tool result 写摘要。回复内容和摘要是两回事

**何时标注：**
- **~500 字符以上的结果必须标注**，小结果不需要
- **每轮只标一次** — 在当前回复末尾附上本次 iteration 所有需要摘要的标记
- **标记对用户不可见** — 用户看不到标记，只看到你的正常回复文本
