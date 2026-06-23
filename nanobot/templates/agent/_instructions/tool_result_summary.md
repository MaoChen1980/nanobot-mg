### Tool Result Summary — 大结果必须加摘要标记

大工具结果会撑爆 context window。**你的摘要标记是保护 context 的唯一手段。**

当你处理完一个大结果(>500字符)后，在**当前回复末尾**附上标记。框架会用你的摘要替换原始工具结果，后续轮次和历史都使用压缩版本。

```
[tool_summary:<call_id>]精简但保留关键事实的摘要[/tool_summary]
```

`call_id` 用该工具调用时的 `id`（如 `call_function_xyz123`）。例如：

```
文件内容我处理完了。关键信息如下：
- loop.py 1647 行，核心方法 run/process/iterate
- 异步架构 + 钩子扩展 + 子代理支持

[tool_summary:call_function_xyz123]loop.py: 1647 行, 核心: run/process/iterate, 异步+钩子架构[/tool_summary]
```

要点：
- **~500 字符以上的结果才需标注**，小结果不需要
- **摘要是替换 tool result 用的**：保留关键事实，控制篇幅
- **只标一次**，后续回复不需要重复
- **标记对用户不可见**，不标 = 大结果一直占用 context
