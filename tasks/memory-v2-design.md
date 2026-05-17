# Memory v2 设计讨论

> 日期: 2026-05-15
> 状态: 设计讨论阶段，未实现

---

## 现有系统的问题

### 当前架构

```
会话消息 → Consolidator(LLM) → 自由文本 summary → DB (history 表)
                                                    ↓
                                            Dream Phase1(LLM) → 重读分析
                                            Dream Phase2(LLM) → 编辑记忆文件
```

（注：history 原用 history.jsonl 文件存储，已迁移至 SQLite DB。——2026-05-10 清理）

### 核心问题

#### 1. 信息两级衰减

- Consolidator 把完整对话压缩成自由文本 summary（第一级有损）
- Dream Phase 1 读取 summary 猜测原意（第二级有损）
- 每一层都在丢失信息，最终产出不可靠

#### 2. LLM 不该管文件结构

- Dream Phase 2 让 LLM 用 AgentRunner 编辑 SOUL.md / USER.md / MEMORY.md
- LLM 不可靠，不会遵守复杂的文件组织规则
- MEMORY.md 实际效果：内容重复、结构混乱，没有建成"索引 + topic 文件"结构
- `memory/<category>/<topic>.md` 分类目录从未被创建出来

#### 3. batch_size 与产物完整性的矛盾

- `max_batch_size=20` 是为了 LLM 注意力集中（质量考量）
- 但 20 条对话凑不够写一个完整 topic 文件或 skill 所需的上下文
- `max_iterations=15` 同样限制：创建 1 个 skill 需读模板 + 去重检查 + 写文件 ≈ 3 次工具调用

#### 4. 工具调用数据被浪费

- session.messages 里有完整的 tool_call/tool_result
- 但框架没有统计这些数据喂给 LLM
- 而是让 LLM 凭"回想"总结工具使用情况，不可靠

#### 5. Consolidator + Dream 重复劳动

- 两个系统各自维护 trigger、cursor、lock
- Consolidator 先压缩 → Dream 再读压缩结果重新分析
- 完全可以合并

---

## 关键洞察

### 洞察 1：半截子消息的分析无效

如果在对话中途切一段给 LLM，它看到"我们决定用 X"，但不知道后面被推翻改成了 Y。它的发现就是错的。

**解法**：只处理"已经稳定的历史段"（距离当前至少 N 条消息），以自然断点（时间间隔 >30 分钟、session 切换）为语义边界。

### 洞察 2：最完整的上下文已经在 prompt 里了

每次发给 LLM 的 `messages[]` 数组已经包含了：
- 系统上下文（SOUL.md / USER.md / MEMORY.md 等）
- 完整对话历史 + 工具调用及结果

**这是最高保真度的信息载体。** 任何 summary、压缩都是降维打击。

**解法**：不必让 Consolidator 压缩后再让 Dream 猜。直接把完整 prompt 存下来，分析时 LLM 看到的是无损原始上下文。

### 洞察 3：LLM 做语义判断，框架做确定性执行

LLM 擅长：理解语义、判断价值、提取事实
框架擅长：文件路径管理、格式保证、一致性维护

**解法**：LLM 输出"发现了什么"（语义的），不输出"写到哪里"（机械的）。框架根据 type + topic 路由到正确文件位置。

---

## 新架构设计

### 核心原则

1. **LLM 只在最富上下文时做一次理解**
2. **后续全部是代码的确定性累积和写入**
3. **结构化数据代替自由文本在阶段间传递**

 ### 流水线

```
每 M 轮对话（发 LLM 前）
    │
    ├──→ 把 messages[] 保存为 .pt 文件
    │     目录：workspace/prompts/
    │     命名：{session_key}-{ts}.pt
    │     含系统 prompt（记忆快照）+ 对话历史 + 本轮用户消息
    │     M=100，重启后归零重新计数
    │
    ▼
MemoryExtractor Cron（每 2 小时）
    │
    ├──→ Step 1: 提取
    │     ├── 扫描 workspace/prompts/*.pt（按文件名排序，旧的先处理）
    │     ├── 逐个 .pt → 改名为 .pt.processing（防重复处理）
    │     ├── LLM 分析 → 校验 JSON
    │     │     ├── 成功 → 收集 findings，删除 .pt.processing
    │     │     └── 失败 → 移入 failed/ 子目录
    │     └── 全部 .pt 处理完后 → 进入 Step 2
    │
    └──→ Step 2: 写入 + 清理
          ├── 写入 findings 到目标文件
          │     ├── USER.md / SOUL.md 追加
          │     ├── knowledge/decision → 查 topic 映射表 → 创建或追加 topic 文件
          │     ├── reusable_pattern → 用 skill-manager 模板跑 LLM 生成 SKILL.md
          │     └── MEMORY.md 自动生成（扫描 memory/ 目录结构）
          ├── 清理 LLM 检查 SOUL.md / USER.md（此时包含刚追加的内容，判断更准）
          ├── 执行清理动作
          ├── git commit（一次）
          └── FAISS 重建（一次）
```

### Topic 映射表

框架维护一份 `workspace/memory/topic-map.json`，记录 `topic → 目录路径` 的映射：

```json
{
  "gateway 架构": "architecture",
  "数据库迁移": "database",
  "nanobot 配置": "config"
}
```

- 第一次遇到新 topic 时：用 tags[0] 作为目录，写入映射表
- 后续遇到同一 topic 时：无论 tags 是什么，都用映射表里的目录
- MEMORY.md 索引由框架自动维护（新建 topic 文件时追加索引链接）

### 不需要的组件

- **Consolidator** — 不再需要，直接保存原始 prompt
- **Dream** — 全部移除
- **DB history 自由文本** — 不再需要，prompt 存档本身就是最高保真度
- **max_iterations** — 文件操作是代码，没有次数限制
- **batch_size** — 不再相关
- **累积器** — 写入前检查文件是否已存在即可

### LLM 输出格式

#### Schema A：分析输出（Analysis）

```python
class Finding(BaseModel):
    type: Literal["user_preference", "soul_rule", "knowledge", "decision", "reusable_pattern", "skip"]
    # 所有 type 都有的字段
    content: str          # 发现的内容
    confidence: Literal["high", "medium"] = "medium"  # 确信度

    # type 相关字段（按 type 决定是否必填）
    condition: str | None = None  # soul_rule: WHEN 条件
    action: str | None = None     # soul_rule: THEN 行动
    topic: str | None = None      # knowledge/decision: 主题名称，用于决定 topic 文件名
    tags: list[str] | None = None # knowledge: 分类标签，用于决定目录
    name: str | None = None       # reusable_pattern: skill 名称（kebab-case）
    steps: list[str] | None = None  # reusable_pattern: 步骤列表
    rationale: str | None = None  # decision: 决策理由

class AnalysisOutput(BaseModel):
    """LLM 分析一个 saved prompt 的输出。"""
    session_summary: str = ""  # 这段对话的整体总结
    findings: list[Finding] = []  # 发现列表
```

#### Schema B：清理输出（Cleanup）

在全部 findings 写入后，对 SOUL.md/USER.md 做一次检查。

```python
class CleanupSuggestion(BaseModel):
    file: Literal["SOUL.md", "USER.md"]
    action: Literal["remove", "rewrite", "keep"]
    reason: str                # 为什么这么操作
    target_text: str           # 要修改的内容（整行或段落）
    replacement: str | None = None  # rewrite 时的新内容

class CleanupOutput(BaseModel):
    """LLM 检查 SOUL.md/USER.md 后的输出。"""
    suggestions: list[CleanupSuggestion] = []
```

### 路由规则

| Finding.type | 目标文件 | 行为 |
|-------------|---------|------|
| user_preference | USER.md 追加 | 追加到文件末尾 |
| soul_rule | SOUL.md 追加 | 追加到文件末尾 |
| knowledge | memory/\<tags[0]\>/\<topic\>.md | 创建 topic 文件，更新 MEMORY.md 索引 |
| decision | memory/\<tags[0]\>/\<topic\>.md | 同上，追加到对应 topic 文件 |
| reusable_pattern | skills/\<name\>/SKILL.md | 用 skill-manager 模板跑一次 LLM 生成完整内容 |
| skip | 无 | 忽略 |

---

## 已确定的决策

| 决策 | 结论 |
|------|------|
| **命名** | `memory_extractor.py` / `MemoryExtractor` / cron ID: `memory_extractor` |
| **保存时机** | 每 M 轮（默认 100），messages[] 发给 LLM **之前**保存 |
| **保存内容** | 完整 messages[]（系统 prompt + 对话历史 + 本轮用户消息），.pt 文件存 workspace/prompts/ |
| **分析 LLM 不需要额外加载记忆文件** | 存档已包含记忆快照 |
| **cron 触发处理**（每 2 小时） | 每次处理所有未处理的 .pt，成功则删除，失败移入 `failed/` 子目录 |
| **Step 1→2 顺序执行** | 提取（全部 .pt）→ 写入 + 清理合并为一步。删除 + 清理在同一次 commit 中完成，一次 FAISS 重建 |
| **失败处理** | 处理失败 → `.pt.processing` 移入 `workspace/prompts/failed/`，不做重试。不用清理 |
| **去重策略** | M=100 降低冲突概率。Step 3 的清理检查处理矛盾/过时/重复内容 |
| **Skill 去重** | 分析 LLM 的 prompt 里包含现有 skills 列表，让它自己判断 |
| **Skill 创建** | reusable_pattern → 用 skill-manager SKILL.md 做模板跑一次 LLM 生成完整内容 |
| **批量写入** | 一次 cron 周期内，攒齐所有 findings 后一次性写入、一次 git commit、一次 FAISS |
| **写入原子性** | 不保证原子性。中途崩溃可接受，设计不为此做容错 |
| **Topic 映射表** | 框架维护 `workspace/memory/topic-map.json`，topic → 目录。写入竞态先忽略 |
| **MEMORY.md 索引** | 自动生成（扫描 memory/ 目录结构）。现有手工内容迁移到独立文档，索引指回 |
| **DB history 旧数据** | 保留不管，后续再考虑利用 |
| **session_key 文件名** | 替换非法字符为 `_`（`[^a-zA-Z0-9_.-]` → `_`），保留可读性 |
| **M 计数器** | per-session，每个通道独立计数 100。重启后归零，没有对话不保存 |
| **分析 LLM 截断** | 输入超过 context window 时截断前面，保留最后的内容 |
| **.pt 文件名格式** | `{session_key}-{YYYY-MM-DDTHH-MM-SS}.pt`，冒号换短横，按名字排序 = 按时间排序 |
| **旧代码移除** | 上线即删（Consolidator + Dream） |
| **清理 prompt** | `templates/agent/extractor_cleanup.md` 已设计定稿 |
| **MEMORY.md 手工内容迁移路径** | `memory/manual/` 目录下按章节拆分文件 |
| **Context window** | 先不管，记录到已知问题文档避免遗忘 |
| **FAISS 重建** | 全局重建，不做增量。以后遇到性能问题了再改 |
| **.pt 文件尺寸** | 不做限制，磁盘足够大 |
| **不需要累积器** | 写入前检查目标文件是否存在即可 |
| **不需要的组件** | Consolidator、Dream、DB history 自由文本、max_iterations、batch_size、累积器 |

## 分析 LLM prompt（已定稿）

```
You are analyzing a conversation snapshot — a "saved prompt."

The saved prompt contains two parts:
1. System prompt — includes the agent's memory files (SOUL.md, USER.md,
   MEMORY.md) as they were at that moment. This is your reference for
   "what was already known."
2. Conversation history — user messages and assistant replies up to that
   point. This is "what happened."

Your job is to identify NEW information — facts, preferences, rules,
decisions, or patterns in the conversation that are NOT already
reflected in the system prompt's memory content.

Do NOT extract things already present in the memory snapshot.
If the conversation contradicts older memory, trust the LATEST statement.

Respond ONLY with a JSON object:

{
  "session_summary": "<concise summary>",
  "findings": [
    {
      "type": "user_preference|soul_rule|knowledge|decision|reusable_pattern|skip",
      "content": "<finding>",
      "confidence": "high|medium",
      // For soul_rule:
      "condition": "<WHEN...>",
      "action": "<THEN...>",
      // For knowledge/decision:
      "topic": "<topic name for file>",
      "tags": ["<category tag>"],
      "rationale": "<why>",  // decision only
      // For reusable_pattern:
      "name": "<kebab-case-name>",
      "steps": ["<step 1>", ...]
    }
  ]
}
```

## Topic 文件格式（已定稿）

knowledge/decision 写入 `memory/<tags[0]>/<topic>.md`：

```markdown
# {topic}

{content paragraph 1}

{content paragraph 2}

---

*创建: {date}*
```

- 不存在则新建，已存在则追加段落
- 每个独立 finding 追加一段
- 无复杂结构，FAISS 直接索引全文
