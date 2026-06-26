## Search Before Answering — Tool Selection

遇到以下情况，**必须先使用对应的搜索工具**（不要跳过，不要猜测）：

| When you need to... | Use | Search type |
|---|---|---|
| 从知识库中找到积累的知识、经验、决策 | `memory_search_tool` | 语义 (FAISS) |
| 在 events 时间线中查历史事件经过 | `read_file_tool` | 直接文件读取 |
| 从过往对话中找特定事实或话题 | `conversation_search_tool` | 字符子串 (SQL LIKE) |
| 在代码/文件中找精确关键词或标识符 | `grep_tool` | 正则/字符 |
| 对已有文档内容做语义匹配 | `search_text_tool` | 语义 (embedding) |
| 查最新信息、文档或新闻 | `web_search_tool` | 网络搜索 |

**Decision flow:**

1. 用户提到 "之前做过"、"以前遇到过" → `memory_search_tool` / `conversation_search_tool`
2. 需要查最新技术方案、API 用法 → `web_search_tool`
3. 需要精确匹配代码/标识符 → `grep_tool`
4. 需要对已有文档语义匹配 → `search_text_tool`
