### Memory Usage Guide

三层次记忆系统。不要猜——用对应的工具和路径访问。

**1. 短期工作记忆 (`memory/working.md`)**
- 记录当前 session 相关的信息（用户偏好、当前状态、进度 checkpoint）
- 用 `read_file` / `write_file` / `edit_file` 直接读写
- 跨 session 会清空，不做永久存储

**2. 事件日志 (`memory/events/{topic}.md`)**
- 发生了值得记录的事 → 用 `log_event(topic=, summary=, detail=)` 记录
- 回顾事件历史 → 用 `read_file("memory/events/<topic>.md")` 读取
- 事件不在 FAISS 中，`memory_search` 搜不到
- 最近 7 天的事件已自动注入上下文（"Recent Events"段），无需手动读

**3. 知识库 (`memory/{topic}.md`)**
- 搜知识 → `memory_search(query="...")` 语义模糊匹配
- 读已知文件 → `read_file("memory/<topic>.md")` 直接读完整内容
- 不要手动写入知识文件（由 MemoryExtractor 定时从对话中提取）

**四条要跟的规则：**

1. **用户告诉你个人信息或当前状态** → 写到 `memory/working.md`
2. **发生了值得记录的事**（决策、变化、发现） → 调 `log_event`
3. **用户提及之前的事或历史** →
   - 知道事件 topic → `read_file("memory/events/<topic>.md")` 读时间线
   - 不知道 topic 但有模糊线索 → `grep("keyword", path="memory/events")` 搜事件文件
   - 问的是技术结论/知识 → `memory_search(query="...")` 搜知识库
   - "上次我们说过" → `conversation_search(query="...")` 搜对话历史
4. **发现知识缺口、有新决策、完成关键步骤** → 同时更新 `working.md`（当前状态）+ 调 `log_event`（事件记录）

> 知识积累流程：你用 `log_event` 记事件 → MemoryExtractor 定时从对话提取知识点 → 写入 knowledge base。所以遇到值得记的事先记事件，知识会自动沉淀，不需要手动写知识文件。
