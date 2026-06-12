## Think Loop

- 不确定方向/假设 → `assess_me_tool()`
- 推理矛盾、工具结果和预期对不上 → `debug_root_cause_tool()`
- 用户请求模糊、能理解成多种方式 → `assess_me_tool()`
- 试了几次、都是同样的结果 → `reframe_tool()`
- 修完了但不确定根因是否解决 → `assess_me_tool()`
- 上下文太多、tool call 输出淹没了对话 → `reframe_tool()`
- 方案越写越复杂、核心没简化 → `reframe_tool()`
