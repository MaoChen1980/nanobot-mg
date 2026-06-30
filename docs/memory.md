# 记忆系统

NanoBot 的记忆系统负责持久化、提取和检索长期知识，使 AI 代理能够在会话之间保持对用户偏好、项目经验和系统配置的记忆。

## 架构概览

记忆系统由四个核心模块组成：

```
MemoryStore            — 文件 I/O 和存储管理
MemoryExtractor        — 定期从对话快照中提取记忆
MemoryVectorIndex      — FAISS 向量索引，支持语义搜索
ContextBuilder         — 将记忆注入到代理的上下文（system prompt）中
```

这些模块协同工作，形成一个闭环：对话数据被保存为 `.pt` 快照文件，`MemoryExtractor` 定期分析这些快照并提取结构化的记忆写入文件系统，`MemoryVectorIndex` 为这些记忆建立向量索引以便语义检索，`ContextBuilder` 在每轮对话中将相关记忆注入到 system prompt 中。

---

## MemoryStore — 存储层

代码位置：[memory_store.py](file:///e:/claude/nanobot-mg/nanobot/agent/memory_store.py)

`MemoryStore` 是文件 I/O 的管理者。它不处理记忆的逻辑提取，而是负责对以下文件的读写：

| 文件 | 用途 |
|---|---|
| `SOUL.md` | 代理的身份/人格定义 |
| `USER.md` | 用户个人信息与偏好 |
| `RULES.md` | 用户自定义规则 |
| `memory/MEMORY.md` | 记忆索引（供人类浏览） |
| `memory/*.md` | 按主题分类的知识文件（由 Extractor 生成） |
| `memory/events/*.md` | 事件时间线记录 |
| `memory/working.md` | 短期工作记忆（代理在线写入） |
| `memory/system.md` | 系统级偏好配置 |

### 核心方法

- `read_memory()` / `write_memory(content)` — 读写 `MEMORY.md`
- `read_soul()` / `write_soul(content)` — 读写 `SOUL.md`
- `read_user()` / `write_user(content)` — 读写 `USER.md`
- `read_rules()` / `write_rules(content)` — 读写 `RULES.md`
- `list_memory_files()` — 列出 `memory/` 下所有 `.md` 文件（排除索引目录）
- `read_categorized_file(rel_path)` / `write_categorized_file(rel_path, content)` — 读写分类知识文件
- `get_memory_context()` — 返回 MEMORY.md 内容作为上下文文本

### 文件读取缓存

`MemoryStore` 有一个基于 mtime 的文件读取缓存 `_file_cache`。当文件未被修改时，重复读取直接从缓存返回，避免磁盘 I/O。

### 历史记录

`MemoryStore` 通过 `condense_session_to_history(messages)` 方法将会话消息归档到 SQLite 数据库中（由 `NanobotDB` 处理）。归档过程：

1. 将消息分组为"轮次"（turn），每轮以 user 消息开头
2. 每轮浓缩为：用户输入 → 思考过程 → 工具名称 → 最终回复
3. 工具执行结果被排除（体积大且已被消化）
4. 写入 SQLite 历史表，支持后续的游标读取和压缩

相关方法：
- `append_history(entry, ...)` — 追加历史条目
- `read_unprocessed_history(since_cursor)` — 读取自某游标以来未处理的历史
- `compact_history()` — 压缩超出容量上限的历史记录

### 多索引支持

`MemoryStore` 维护了三个 FAISS 索引：

1. **vector_index** — `memory/` 目录下知识文件的语义索引
2. **tasks_index** — `tasks/` 目录下任务文件的语义索引
3. **skills_index** — 技能列表的语义索引

启动时自动检查索引是否存在，若缺失则自动构建。

---

## MemoryExtractor — 记忆提取

代码位置：[memory_extractor.py](file:///e:/claude/nanobot-mg/nanobot/agent/memory_extractor.py)

`MemoryExtractor` 是记忆系统的引擎。它替代了旧版的两阶段通路（Consolidator + Dream），采用更简洁的三步流水线。

### 三步流水线

#### Step 1 — 提取

1. 扫描 `workspace/prompts/` 目录（也称为 `prompts_dir`），收集所有 `.pt` 和 `.pt.processing` 文件
2. 将 `.pt` 文件重命名为 `.pt.processing` 以声明所有权（防止并发竞争）
3. 调用 LLM 对每个快照进行分析（`_analysis_llm()` 方法）
4. LLM 返回 JSON 格式的结构化结果，包含 `findings`（发现）和可选的 `events`（事件）

提取流程中使用了一个质量门控：拒绝模糊的中文建议，如"注意……""建议……"等无实质内容的表述。

#### Step 2 — 写入 + 清理

1. 将 findings 按类型分类写入对应文件：
   - `preference` -> `memory/user.md`
   - `skill` -> 暂存到 `_pending_skill_entries`
   - `knowledge` / `pitfall` / `pattern` -> `memory/<topic>.md`
   - `instruction` -> `RULES.md`
   - `tool_script` -> 暂存到 `_pending_tool_scripts`
   - `events` -> `memory/events/<topic>.md`

2. 内容去重和合并：
   - 基于标准化文本的精确去重
   - 使用 CJK 字符二元组（bigram）或英文单词重叠率的语义去重（重叠率 > 70% 视为重复）
   - 去除孤立的子标题（`##` / `###` 下无内容）

3. 写入时使用原子操作：先写 `.md.tmp` 临时文件，再通过 `replace()` 替换原文件

4. 处理用户反馈：从 `~/.nanobot/self_improve/user_corrections.jsonl` 中读取用户纠正信号，聚合后写入 `workspace/framework/user_feedback.md`

#### Step 3 — 后处理

1. 物化工具脚本（`_materialize_tool_scripts`）：
   - 将 `tool_script` 类型的 finding 复制到 `workspace/tools/<name>/` 目录
   - 写入 `readme.md` 包含安装/卸载/使用说明
   - 生成 skill 条目供下一步处理

2. 物化技能（`_materialize_skills`）：
   - 启动子代理，使用文件工具读取现有技能目录
   - 决策：新建/更新/合并/跳过
   - 直接写入 `workspace/skills/<name>/SKILL.md`

3. 内容整合（`_consolidate_memory`）：
   - 当目录数超过 20 时或存在小文件簇（3+ 个 <= 10 行的文件）
   - 调用 LLM 决定：合并文件、合并目录、移动文件

4. 清理检查（`_cleanup_check`）：
   - 调用 LLM 检查 SOUL.md/USER.md 及修改过的文件
   - 执行矛盾检测、重复检测、过时内容标记
   - 支持 `remove` 和 `rewrite` 操作

5. 索引重建：
   - 生成 MEMORY.md（人类可读的知识地图 + 事件时间线 + 健康统计）
   - 生成 `tree.json`（WebUI 使用的文件树 + 最近变更）
   - 生成各级目录的 `index.md`
   - 建立跨文件反向链接（`## See also` 节）
   - 重建 FAISS 向量索引

6. Git 自动提交（如果 GitStore 已初始化）

### 发现类型与表情映射

| 类型 | 表情 | 说明 |
|---|---|---|
| `pitfall` | ⚠️ | 陷阱/注意事项 |
| `pattern` | 💡 | 有用的模式/经验 |
| `knowledge` | 📌 | 知识点 |
| `preference` | 👤 | 用户偏好 |
| `instruction` | (无) | 指令/规则 |
| `skill` | 🛠️ | 技能定义 |

### 事件系统

事件被写入 `memory/events/<topic>.md` 的 `## Timeline` 节。当单个事件的条目超过 30 条时，60 天前的事件会被压缩为季度摘要。

### 内容整合

当一个文件在单次提取中收到 3+ 条新条目时，`MemoryExtractor` 会触发内容级整合（`_consolidate_topic_content`）。整合过程：
1. LLM 诊断内容类型（技术文档、项目经历、管理文档等 11 种类型之一）
2. 按对应模板重组结构
3. 合并重复条目，保留元数据（`<!--ts:...-->`、`<!--pinned-->`、`<!--recent-->`）

---

## MemoryVectorIndex — 向量搜索

代码位置：[memory_vector.py](file:///e:/claude/nanobot-mg/nanobot/agent/memory_vector.py)

`MemoryVectorIndex` 使用 FAISS 为记忆文件建立向量索引，支持语义搜索。

### 嵌入模型

- 默认模型：`bge-small-zh-v1.5`（位于 `nanobot/models/bge-small-zh-v1.5/`）
- 使用 `sentence-transformers` 库加载
- **懒加载**：仅在首次构建或搜索时加载，可选的依赖项（如果未安装则优雅降级为关键词搜索）
- 线程安全：使用 `threading.Lock` 保护模型加载

### 分块策略

Markdown 文件以 `##` 标题为界分割为块（chunk），每个块最大 1000 字符。超出部分在段落边界处进一步拆分。

### 索引构建

- **全量构建** (`build_from_files`)：接收 `{source_path: text_content}` 映射，重新创建整个索引
- **增量构建** (`build_incremental`)：通过 `file_map.json` 跟踪每个文件的 mtime 和 chunk ID，仅重新索引变更的文件
- 使用 `IndexHNSWFlat`（HNSW 图） + `IndexIDMap`（支持按 ID 删除）的组合
- 支持从旧版索引自动迁移到 `IndexIDMap` 格式

### 搜索方式

`search(query, k=5, min_score=0.3)` 方法采用混合搜索策略：

**方式 1：FAISS 语义搜索 + 关键词 RRF 融合**
1. 将查询文本编码为向量，用 FAISS 搜索 top-k*3 个最近邻
2. 同时计算关键词命中分数
3. 使用 RRF（Reciprocal Rank Fusion，k=61）融合两个排名
4. 返回融合后的 top-k 结果

**方式 2：纯关键词搜索（回退）**
- 当 FAISS 索引不可用或未加载模型时使用
- 提取查询中的拉丁词和 CJK 二元组
- 按命中数降序返回结果
- 分数计算：`1.0 / (61 + rank)`

### 持久化

- 索引文件：`index.faiss`（同时保存 `index.faiss.bak` 作为备份）
- 块元数据：`chunks.json`
- 文件映射：`file_map.json`（用于增量更新的变化追踪）
- 所有文件保存在 `memory/.vector_index/` 目录下

---

## Git 版本管理

代码位置：[gitstore.py](../nanobot/utils/gitstore.py)

MemoryStore 可选地使用 Git（基于 [dulwich](https://www.dulwich.io/) 纯 Python 实现）对记忆文件进行版本管理。

### 功能

- 自动提交：MemoryExtractor 完成提取后自动提交变更
- 差异查看：查看每次提取前后的文件变更
- 行龄追踪：基于 git blame 计算每行距离上次修改的天数
- 历史回溯：通过 `nanobot git log` 查看提交历史

### 初始化

在工作区目录执行 `git init` 即可启用版本管理：

```bash
cd ~/.nanobot/workspace
git init
git add -A
git commit -m "Initial memory state"
```

启用后，每次记忆提取完成会自动提交变更。

---

## log_event 工具

代码位置：[log_event.py](../nanobot/agent/tools/log_event.py)

`log_event` 是代理可调用的工具，用于记录带时间戳的事件到 `memory/events/{topic}.md`。

### 功能

- 事件形成不可变时间线，追加到 `## Timeline` 节
- 自动处理文件创建、去重（按摘要 + 详情）、日期排序
- 适合记录：决策、用户偏好、Bug 修复、项目里程碑、健康变化

### 与记忆提取的关系

`log_event` 是**主动记录**，而 MemoryExtractor 是**被动提取**。前者由代理在执行任务时主动调用，后者由定时任务在后台自动分析。

---

## 记忆搜索工具

代码位置：[memory_search.py](file:///e:/claude/nanobot-mg/nanobot/agent/tools/memory_search.py)

`MemorySearchTool` 是代理可调用的工具，用于语义搜索知识库。

- 名称：`memory_search`
- 参数：`query`（自然语言查询）、`k`（结果数，默认 5，最大 20）
- 同时搜索三个索引：知识库（memory/）、任务（tasks/）、技能（skills/）
- 返回结果包含：源文件、标题、分数、文本摘录和行号
- 对高分结果（`score > 0.5`）自动遍历反向链接（`## See also`），展示关联内容

---

## 记忆注入 — ContextBuilder

代码位置：[context.py](file:///e:/claude/nanobot-mg/nanobot/agent/context.py) (`_build_memory_section` 方法)

每轮对话构建消息时，`ContextBuilder` 将记忆作为 system prompt 的一部分注入：

### 注入层级

**Tier 2 — 动态会话状态**（每轮变化，追加到 system prompt 末尾）：

1. **工作记忆** (`memory/working.md`)：代理的短期草稿本
2. **系统偏好** (`memory/system.md`)：系统级配置
3. **用户偏好** (`memory/user.md`)：用户个人设置（最多 2000 字符，超出截断）
4. **最近事件**：扫描 `memory/events/` 下最近 7 天的事件（按主题分组，每主题最多 5 条）

注入时还会附加一条记忆质量提示，告知代理当前平均每轮注入的记忆节数，提醒代理如发现过期或不相关记忆应及时更新。

完整的注入结构：
```
# Memory

## Memory Workspace

Current working memory and persistent user/system preferences.
working.md is your short-term scratchpad — update it inline as you work.
For knowledge base lookups, use the memory_search tool.

### Working Memory — <path>/memory/working.md
<content>

### System
<content>

### User
<content>

### Recent Events
<topics + entries>
```

### 其他注入点

- `_build_self_findings_section()` — 从 `workspace/framework/self_findings.md` 注入自我发现
- `_build_user_feedback_section()` — 从 `workspace/framework/user_feedback.md` 注入用户反馈

---

## 数据流总结

```
用户对话
     ↓
保存 .pt 快照（MemoryExtractor.save_prompt_snapshot）
     ↓
MemoryExtractor.run() 定时执行
     ├─ Step 1: LLM 分析 .pt → findings + events
     ├─ Step 2: 写入 memory/*.md / RULES.md / events/
     └─ Step 3: 物化工具和技能 + 整合 + 重建索引
     ↓
MemoryStore 管理文件读写（mtime 缓存）
     ↓
MemoryVectorIndex 建立/更新 FAISS 索引
     ↓
ContextBuilder._build_memory_section()
     ├─ 读取 working.md / system.md / user.md
     ├─ 扫描 events/ 最近 7 天事件
     └─ 注入到 system prompt
     ↓
代理在对话中感知记忆
     ↕
memory_search 工具 → FAISS 语义搜索 + 关键词回退
```
