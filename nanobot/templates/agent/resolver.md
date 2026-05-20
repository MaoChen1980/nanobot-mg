## Search Tool Selector

Choose the right tool for your search need:

| When you need to... | Use | Why |
|---|---|---|
| Find **framework docs, behavioral rules, constraints** | `framework_search` | FAISS index of framework/ — 100% accurate, must follow |
| Find **exact keywords** in code, configs, or files | `grep` | Regex, file patterns, line numbers |
| **Semantically search** one long document or file | `search_text` | Embedding similarity within a single text |
| **Semantically search** the entire memory/ knowledge base | `memory_search` | FAISS vector index across all memory files + keyword boost + cross-refs to related files |
| **Search conversation history** (past sessions) | `conversation_search` | Keyword + date range against SQLite history |

**Decision flow:**

1. Need to understand framework rules/constraints? → `framework_search`
2. Need **exact** match (code, known term, identifier)? → `grep`
3. Need **meaning match** in a specific document you already have? → `search_text`
4. Need **meaning match** in accumulated knowledge? → `memory_search`
5. Need to find **when something happened** in past conversations? → `conversation_search`

**memory_search notes:**
- Results include cross-reference links to related memory files when relevance > 0.5
- Hybrid FAISS + keyword for better recall than pure vector search
- New memory content appears after the next extractor cycle (up to 2h delay)
- For precise known terms within memory, use `grep memory/` instead
