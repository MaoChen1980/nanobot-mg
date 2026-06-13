### Search Tool Selector
根据搜索需求选择合适的工具：
- 需要 framework 规则/约束 → framework_search_tool（FAISS 语义搜索 {{ workspace_path }}/framework/）
- 需要精确匹配（代码、标识符）→ grep_tool（正则/字符）
- 需要已有文档内语义搜索 → search_text_tool（embedding 相似度）
- 需要知识库语义搜索 → memory_search_tool（FAISS + 关键词混合）
- 需要历史对话搜索 → conversation_search_tool（SQL LIKE 子串匹配）

Decision flow:
1. 需要 framework 规则/约束？→ framework_search_tool
2. 需要精确匹配（代码、已知术语）？→ grep_tool
3. 需要已有特定文档中语义匹配？→ search_text_tool
4. 需要积累的知识中语义匹配？→ memory_search_tool
5. 需要过去对话中特定文本？→ conversation_search_tool
