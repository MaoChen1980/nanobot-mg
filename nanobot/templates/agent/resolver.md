## Search Tool Selector

根据搜索需求选择合适的工具：

| When you need to... | Use | Search type | Why |
|---|---|---|---|---|
| 查找**行为规则、约束、知识** | `memory_search` | **语义** (FAISS) | `{{ workspace_path }}/memory/` 的 FAISS 索引——语义匹配积累的知识 |
| 在代码、配置或文件中查找**精确关键词** | `grep` | **正则/字符** | 支持正则、文件模式、行号 |
| 对单个长文档或文件进行**语义搜索** | `semantic_search` | **语义** (embedding) | 单个文本内的 embedding 相似度 |
| 对整个 memory/knowledge base 进行**语义搜索** | `memory_search` | **语义** (FAISS) | 跨所有 memory 文件的 FAISS 向量索引 + 关键词增强 + 相关文件交叉引用 |
| 搜索**对话历史**（过往 session） | `conversation_search` | **字符子串** (SQL LIKE) | 基于关键词 + 日期范围的 SQLite 历史查询；支持 `\|` OR |

**Decision flow:**

1. 需要**精确**匹配（代码、已知术语、标识符）？→ `grep`（字符/正则）
2. 需要在已有特定文档中进行**语义匹配**？→ `semantic_search`（语义）
3. 需要在积累的知识中进行**语义匹配**？→ `memory_search`（语义）
4. 需要查找过去对话中**特定的文本/事实**？→ `conversation_search`（字符子串 LIKE）

**Query patterns — match section heading granularity, use specific terms:**

| Tool | Good query | Why it works |
|------|-----------|-------------|
| `memory_search` | `android build gradle apk config` | 匹配 `## 构建工具` 章节 |
| `memory_search` | `memory extraction consolidation` | 自然短语匹配章节粒度 |
| `semantic_search` | `"subagent orchestration"` | 精确短语用引号包裹 |

Avoid: 单个模糊词（`memory`、`rules`、`thing`）返回噪音。避免完整句子（填充词稀释 embedding）。

**memory_search notes:**
- 相关性 > 0.5 时，结果包含相关 memory 文件的交叉引用链接
- FAISS + 关键词混合策略，召回率优于纯向量搜索
- 新的 memory 内容在下一次 extractor 周期后生效（最长 2 小时延迟）
- 在 memory 中查找精确的已知术语时，改用 `grep`
