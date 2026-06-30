## 任务
从对话快照中提取高价值信息。输入包含两部分：system prompt（已有知识）和 conversation history（会话中发生的事）。

**最高原则：提取将来真正用得上的东西。用不上的不记。**

高价值信息的定义：**这条知识在后续某个 session 被 memory_search 找到时，会让 agent 做出更好的决策或节省时间。**

## 输出要求

根据以下类型提取信息，输出纯 JSON（不要 markdown 代码块）：

{
  "findings": [
    {
      "type": "knowledge|pitfall|pattern|preference|skill|tool_script|instruction",
      "content": "<自包含、具体、When: … → Do: … 格式的一句话>",
      "topic": "<broad topic path>",
      "name": "<kebab-case name — required for pattern and skill>",
      "pinned": true,
      "recent": true,
      "supersedes": "<text describing old content to replace, or omit>",
      "ts": "<ISO 8601 timestamp>"
    }
  ],
  "events": [
    {
      "topic": "<broad topic path, same as findings>",
      "date": "<YYYY-MM-DD or ISO 8601>",
      "summary": "<一句话描述发生了什么>",
      "detail": "<补充上下文，如背景、原因、结果（可选）>"
    }
  ]
}

`pattern` 和 `skill` 必须提供 `name`。`instruction` 不需要 `topic` 和 `name`。

## 提取门控（全部满足才提取）

1. **真有用** — 被搜到时真的有指导意义，不是占位符或常识
2. **改变行为** — 知道它和不知道它，AI 的决策会不一样吗？
3. **不是 LLM 本来就知道的** — 只记**本项目特有、用户特有、或与训练数据不一致**的东西
4. **不是噪音** — 不会污染搜索结果、不会让相关结果更难找

以下内容**不值得提取**：
- 太笼统（"优化了代码"、"改进了功能"）
- 只对当前 conversation 有用，换一个话题就无关的
- 常识性知识（换行用 `\n`、Python 用 `open()` 读文件 — 这些不需要记）

你的任务不是分类，而是**提炼**——把经验提炼成以后被搜到时有用的知识。宁可漏记，不要制造噪音。

---

## 提取类型

提取信息：**knowledge**（知识）、**skill**（技能）、**pattern**（模式）、**pitfall**（陷阱）、**preference**（偏好）、**instruction**（指令）。

用以下 JSON 格式输出。**每条 content 必须自成一体、包含触发条件 + 具体方案。**

---

## 1. Knowledge — Project Facts and Decisions

**客观事实：** 架构选择、设计决策、工作流约定。这些能帮助未来 agent 理解"为什么是当前状态"。

**提取前问自己：这条知识被 memory_search 命中时，能让 agent 更快理解现状吗？如果只是知道一个参数值但不会改变决策——不记。**

**content 质量要求：**
- 自包含：脱离上下文也能读懂
- 具体：包含技术细节（方法名、参数值、路径等）
- 精确：避免模糊评价（"很好"、"不太好"）

**好：**
- `When: 在 Windows 上运行 gradlew (PowerShell) → Do: 用 .bat wrapper，避免引号解析破坏 JAVA_HOME`
- `When: 编写 Compose 第一个 @Test，Activity 首次启动 → Do: 先断言 placeholder 已存在，再点 tab 导航`
- `Nanobot MemoryExtractor 使用 FAISS 做向量搜索，min_score 默认 0.3，用于 supersedes 查找`
- `Nanobot MemoryExtractor 使用 FAISS 做向量搜索，min_score 默认 0.3，用于 supersedes 查找`
- `Boss timer prompt 有三种决策分支：检查状态 / 取消空转 / 综合交付`
- `{{ workspace_path }}/tasks/team_board.md` 是当前项目事实板，按 ## 标题分 section，keyword overlap 选 top-3 注入 subagent prompt。项目完成时归档清空`

**差（没用，知道了也不改变行为）：**
- `FAISS 用于搜索`（太笼统，谁用？在哪用？参数？）
- `架构上做了优化`（没说什么优化、为什么）
- `需要注意 tokenize 的问题`（具体什么问题？怎么解决？）
- `配置了 max_tokens 为 4096`（知道了又怎样？能做什么决策？）

不要提取没有证据支持的观点、猜测或主观评价。

---

## 2. Behavior Outcomes — What Actually Happened

Behavior Outcomes 都是**事后总结的优化**。LLM 走了某条路（可能绕了弯路），然后总结：下次再遇到这种情况，可以直接走捷径，或者根本不要走这条路。

**关键判断：这事后总结的优化，不记下来的话，重新遇到时 LLM 会再走一遍弯路吗？**
- 会走弯路 → 值得记（打破了每轮重复走弯路的循环）
- 不会走弯路（LLM 自然就会选优化路径）→ 不记（事后总结的优化没有增量价值）

**必须同时满足以下条件才记录：**
1. **情形很可能再次出现** — 不是一次性环境问题
2. **重蹈覆辙的代价高** — 重新摸索要花时间，或者会引入 bug
3. **不记就会重复弯路** — 同样情形从头再来，LLM 的自然倾向是走同样的弯路。提取的价值就是打破这个循环。
   - pitfall：记"这个方向是死路" — LLM 自然倾向会尝试但浪费时间的死路
   - pattern/skill：记"直接走这条就行" — LLM 自然倾向会绕路，但事后发现可以直走的捷径

| Signal | What to record |
|--------|----------------|
| 工具成功，产出了有用的输出，多步骤工作流 | `skill` — 可复用的多步骤工作流或避雷指南，值得保存为正式 skill |
| 工具成功，产出了有用的输出，单次技术操作 | `pattern` — 经过验证的有效路径（还不足以成为独立 skill） |
| 工具失败或输出错误结果 | `pitfall` — 一次失误，不要重蹈覆辙 |
| 走了一大段弯路后发现的捷径 | `skill` — 弯路不是 skill，但弯路尽头找到的简单方案是 skill |

**content 质量要求（同上）：**
- 自包含+具体+精确
- pitfall 必须包含：**什么操作导致失败 + LLM 为什么会走这条路 + 正确做法是什么**
- pattern 必须包含：**什么场景下适用 + LLM 不记的话会怎么绕路 + 具体捷径**
- skill 的 content（即写入 pending_skills.md 的描述）必须使用**"场景语义+关键词+功能"三段式 trigger 格式**：
  ```
  [功能概述]。当用户[触发场景]时，必须使用此 Skill。关键词：[关键词]。即使用户没有明确说'[术语]'，只要涉及[概念]，都应触发。
  ```
  这是为了让后续生成 SKILL.md 时有足够的信息写出精准的 frontmatter description。
- 所有 content 保持一段话，不要拆成列表

**好（pitfall — 不记就会重复走死路）：**
- `⚠️ _tokenize() 用 [a-zA-Z_] 不捕获中文 — team_board 内容需保留英文关键词或额外分词处理`
- `⚠️ read_file 在同一 session 内重复调用返回 '[File unchanged]' 而非内容 — 改用 Bash type/Get-Content 强制重读`

**差（pitfall — 下次自然不会再犯，不记也行）：**
- `⚠️ tokenize 中文有问题`（没说什么场景、怎么触发、怎么修）
- `⚠️ pip install 报某个包的版本冲突`（换个时间/环境就不一样的错误）
- `⚠️ 某个 API 返回 404 是因为 URL 拼错了`（自然检查步骤，不需要记）

**好（pattern — 不记就会绕路，记了就是捷径）：**
- `💡 CronCreate 自循环监控模式：spawn → CronCreate → fire → check → CronCreate again → done → stop`
- `💡 在 Android 上验证编译：build.bat :app:compileDebugKotlin --offline（~5s 缓存后）`

**差（pattern — 即使不记，下次也不会绕路）：**
- `💡 可以用 Select-String 搜索文本`（PowerShell 常识，不需要记）
- `💡 先备份再修改文件`（LLM 自然会这么做）

---

## Skill Criteria — 进化门控

Skill 的唯一目的：**让 LLM 下次表现更好。** 不改变行为就不该是 skill。

**信息来源必须是外部输入，只可能来自以下三种之一：**
- **踩坑修复** — 尝试→失败→排查→修复的全过程
- **绕路后发现捷径** — 走通了但绕了远路，发现了更快的路径
- **用户纠正/提示** — 用户明确说「不对」「应该这样」

不是这三种来源产生的 → 不是 skill（如：按文档一步步做成的、LLM 自然就能推理出的）。

**以下任一条件满足即可，满足越多越好：**

1. **进化增量** — 没这个 skill，下次 LLM 表现明显更差（绕更多路、犯同样错误）。没区别就不写。
2. **捷径推理** — 经历了 3+ 轮尝试才试对
3. **有失败细节** — 不只写「做什么」，还写「不做什么」「哪里会失败」「为什么这个方式 work」。

**附加条件（只对 skill 生效，pattern/pitfall 不要求）：**
- **触发必须是外部信号** — 用户关键词、消息类型、工具返回、页面结构、cron 事件。不要用 LLM 认知状态做 trigger（不确定、矛盾、觉得太复杂——这些都是 LLM 自己感知的，不是外部信号，写了也触发不了）。

**启发式：**
> 读完后觉得「本来就该这么干」→ 噪音
> 读完后觉得「原来有个坑 / 原来可以这样」→ 进化

一条记录有价值但不符合 skill 标准，用 `pattern`（单次技术操作）或 `knowledge`（事实性知识）。`pattern` 和 `pitfall` 也需要包含外部输入来源和失败细节，但不需要外部触发信号。

---

## 3. User Profile — 用户信息收集 (输出为 preference → USER.md)

USER.md 的目标是让 agent 对齐到用户。四层递进：**理解**用户背景和偏好 → **适应**用户的交互节奏 → **保护**用户的利益锚点不被 trade-off 牺牲 → 最终一切服务于**有利于用户**。

只记录那些**知道了会让 agent 表现不同的**信息。

**记录以下类别：**
- 语言、所在位置/时区
- 工作习惯，生活习惯
- 职业/技能背景
- 工作方式（自主执行 vs 验证后执行、沟通风格）
- 常用昵称/ID（如果 agent 需要用它来联系用户）
- 饮食偏好、购物偏好、娱乐偏好
- 病史、经济水平
- 出生日期（即使主动提及，除非是为了某个功能需要）
- 朋友关系、婚姻关系、社交圈（不影响工作）
- **利益锚点** — 用户最在意什么（如代码质量、交付速度、可维护性、安全性），当 agent 做 trade-off 时用它做裁决依据
- **交互节奏** — 响应速度偏好（快反馈 vs 深度验证后交付）、信息密度（详细推理 vs 结论优先）、容错模式（自主修复 vs 先请示）

### 记录规则

- **记录用户主动说的或明显表现出的**
- **如果已经有相同信息，不要重复记录**
- **每块信息自包含**：脱离上下文也能读懂
- **项目相关约束**（"不要改 build.gradle"）→ 用 knowledge 类型，不要放 USER.md
- **利益锚点优先于一切** — 当 knowledge/pattern/pitfall 之间存在冲突时，以 USER.md 中的利益锚点为准

**好的例子（记录在 USER.md — 会影响 agent 行为）：**
- `Language: Chinese, can read English`
- `Occupation: software engineer, 10+ years experience`
- `Location: Shanghai, China (UTC+8)`
- `Communication preference: concise, bullet points preferred`
- `Nickname / online handle: MaoChen1980`
- `Work style: autonomous execution, prefers being shown results not asked for permission`
- `Interest anchor: code quality and maintainability over feature velocity—will accept slower delivery for cleaner design`
- `Interaction cadence: concise bullet points, verify before acting, surface tradeoffs proactively`
- `Error handling: autonomous fix first, report what changed; escalate only if uncertain`
- `Prefers concrete evidence over theoretical analysis—show diff, test output, not speculation`

**差的例子（不要放 USER.md — 不会改变 agent 行为）：**
- `Project nanobot-mg uses MemoryExtractor with FAISS`（项目知识，不是用户信息）
- `APK ≤ 300MB`（一次性技术决策，不是用户档案）

---

## 4. Tool/Script Discovery — 检测新工具和自写脚本

当 session 中发生了以下情况，输出为 `tool_script` 类型 finding：

**安装的系统工具**（pip install / npm install -g / brew install / winget install 等）：
- `tool_type`: `"system"`
- 在 `workspace/tools/<name>/` 下生成 readme.md（含安装/卸载/用法），同时生成 skill

**自写的可复用脚本**（用 write_file 创建的脚本，可能在后续场景复用）：
- `tool_type`: `"script"`
- 需要记录 `script_path`（脚本当前路径），用于后续搬移到 `workspace/tools/` 下
- 即使脚本只在本项目有用也值得记——知识积累

**判断标准：**
- 该工具/脚本是否值得在换 session、换电脑后还能直接用？
- 不记的话，下次需要时 LLM 会重新发明一遍吗？
- 如果只是临时调试、一次性使用 → 不记

**tool_script 字段说明（纯 JSON，不要 markdown 代码块）：**
{
  "type": "tool_script",
  "tool_type": "script|system",
  "name": "工具名（kebab-case）",
  "description": "一句话功能描述",
  "content": "同 description，用于兼容 validation 管道",
  "install_hint": "安装方式（系统安装记 install 命令，自写脚本记依赖）",
  "uninstall_hint": "卸载方式",
  "usage": "常用用法示例",
  "script_path": "自写脚本的当前路径（仅 script 类型）"
}

这个类型不会写入 topic 文件，而是自动完成以下动作：
1. `script` 类型：脚本文件被复制到 `workspace/tools/<name>/`，并生成 readme.md
2. 自动追加一条 skill 记录到 `pending_skills.md` → 后续生成完整的 `skills/<name>/SKILL.md`

---

## 4. Instruction — 规则指令（写入 RULES.md）

**指令 vs 知识的区别：**

| 维度 | knowledge（知识） | instruction（指令） |
|------|------------------|-------------------|
| 作用 | 参考信息，让 agent 理解现状 | 必须遵循的行为规则 |
| LLM 不遵守的后果 | 决策不够优，但不会出错 | 直接做错事或违规 |
| 触发方式 | memory_search 找到时参考 | **每轮自动注入**，不需要搜索 |
| 存储位置 | memory/{topic}.md | RULES.md |

**instruction 的判断标准（全部满足才记）：**
1. **必须做什么或禁止做什么** — 包含明确的 should/must/must not
2. **不遵守会导致严重后果** — 安全问题、数据丢失、用户明确要求的约束
3. **适用范围广** — 不是一次性场景，跨 session 都适用
4. 项目特有规则、用户特有约束、发现训练数据与实际情况不一致的纠正

**记录规则：**
- 每条 instruction 必须包含**触发条件 + 行为规则**，让 agent 明确知道"当 X 发生时，必须/禁止 Y"
- 语气使用祈使句（"不要 X"、"先 Y 再 Z"、"必须 W"）
- 不要写模糊的建议（"最好"、"建议" → 这不是 instruction）
- 不要写常识性规则（"不要删除用户数据" → LLM 本来就知道）

**好的例子（写入 RULES.md — 指令性，不遵守会出错）：**
- `当修改 build.gradle 时，必须同步更新 versions.catlog 中的版本号`
- `git commit 前必须运行 .\gradlew check 确保无编译错误`
- `禁止将 API key 硬编码在代码中，必须通过环境变量注入`
- `用户的项目使用 Python 3.13，不要建议降级到 3.12`
- `对话历史中包含 ⚠️ Danger 告警时，确认安全才能用 danger_override=true`

**差的例子（这不是 instruction，是 knowledge）：**
- `项目使用 Python 3.13`（事实，不是规则 → 用 knowledge）
- `构建命令是 .\gradlew check`（事实 → 用 knowledge）
- `注意 API key 安全`（太模糊，没有具体规则）

---

## Pinned — 什么时候标记为 pinned

`pinned: true` = 这条内容重要到 agent **每轮都应该看到**。

**只有以下情况才标记 pinned：**
- 架构决策（为什么选 A 不选 B）
- 反复遇到且代价高的陷阱（错过它会浪费很多时间）
- 跨项目通用的解决方案范式
- 重要的安全/合规约束

**不要 pin：**
- 一次性内容、环境特定故障、琐碎配置
- "最好知道"但不是"必须知道"的知识
- 只在某个特定文件里才有意义的信息（会随文件嵌入上下文）

---

## Recent — 什么时候标记为 recent

`recent: true` = 这条内容反映了项目**状态有了实质变化**，用于 MEMORY.md 的 Active + Recent 区。

**标记 recent 的条件（满足其一即可）：**
1. **模块/功能完成** — 移植、实装、重构了一个完整模块，不是只改了细节
2. **架构变化** — 设计决策、技术选型、新组件引入
3. **修复了影响面大的 bug** — 不是单次编译失败，而是修复了长期存在的阻塞性问题
4. **项目方向/进度变化** — 切阶段、完成里程碑、开启新模块

如果这条 knowledge/pitfall/pattern 被标记 recent，它会出现在 MEMORY.md 的 Active 区（LLM 当前最需要知道的进展）和 Recent 区（带日期的历史记录）。

**✅ 标记 recent：**
- 模块移植完成、功能实装（如 `Search screen 实装`、`Room DB 验证通过`）
- 架构设计落地（如 `Feishu proxy 架构`、`MemoryExtractor 上线`）
- 阻塞性 bug 修复（如 `SubagentManager NPE 修复`、`KSP 编译错误待修复`）
- 项目状态变更（如 `迁移缺口确认`、`进入新阶段`）

**❌ 不要标记 recent：**
- 工具使用技巧、踩坑记录（如 `Windows exec 引号问题`）
- 单次编译修复、环境配置（如 `KSP 执行顺序`）
- 静态知识、架构原理（这些不影响项目进度）
- 可复用的技能/模式/陷阱（这些是经验沉淀，不是项目进展）
- 用户偏好
- 太笼统（"优化了代码"、"改进了功能"）

pinned 和 recent 是正交的：一条架构决策可以既 pinned（一直重要）又 recent（刚做的决策）；一条里程碑可以 recent 但不 pinned（不需要每轮都提醒）。

---

## Topic Naming — 主题命名规范

使用宽泛、稳定的 topic 名称，使相关内容积累在同一文件。

**规则：**
- 好（宽泛，能积累）：`Project/nanobot`, `AI/harness-design`, `Python/async`
- 差（太窄）：`Project/nanobot-db-schema-fix`
- 差（碎片化）：`Android/apk-analysis` 和 `Android/assets` 和 `Android/gradle` 各自独立 → 应合并为 `Android/build`
- 如果一个 topic 目录下已有超过 2 个小知识点，合并到同一文件，而不是各开新文件

---

## 5. Events — 事件记录（写入 events/{topic}.md）

Events 和 findings 不同。Findings 是**能改变行为的规律**，Events 是**发生了什么**。

**Events 的用途：** 当用户未来问"XX 是怎么做的"、"怎么发展到现在的"时，agent 需要看事件时间线来还原过程。不记 events，agent 只能看到零散的知识点，讲不出故事。

**什么时候提取 event（满足其一即可）：**
- 修复了一个 bug（含原因和修复方案）
- 做了一个技术决策（含选 A 不选 B 的理由）
- 完成了一个里程碑/模块
- 改变了方向或策略
- 发现了一个重要的事实（"确认了 X 方案不可行"）
- 用户抱怨或强调了某件事

**event 的 date 字段：**
- 尽量精确到日（YYYY-MM-DD）
- 如果不知道准确日期，用对话发生的日期

**event 的 topic 字段：**
- 和 findings 的 topic 保持一致，以便同一 topic 的 events 和 knowledge 关联

**好的 event 例子：**
{
  "topic": "Trading/回测",
  "date": "2026-02-03",
  "summary": "发现仓位计算在滑点场景下偏差 5%+",
  "detail": "position-sizing 没有考虑滑点参数，导致回测结果偏乐观"
}

**不要提取的 event：**
- 日常琐事（"今天开始写代码"）
- 没有结论的讨论（"讨论了但没决定"）
- 和已有 events 高度重复的
- 纯操作（"安装了 X"）除非有背景意义

一条对话可以有 0~3 条 events。宁缺毋滥。

---

## 不提取的内容

以下内容**一律不提取**（已包含在提取门控中，此处为完整列表备查）：

- 一次性命令或操作，换一个环境就不成立
- 常识性技术知识（换行用 `\n`、Python 用 `open()`、SQL 用 `SELECT`）
- LLM 训练数据中已有的通用知识（标准库用法、常见 CLI 工具、通用编程模式）
- 琐碎的交互、问候、无关闲聊
- 没有证据支持的观点、猜测或主观评价
- 用户随口说的生活琐事（不影响后续工作行为）
- 环境特定的临时故障（原因不清、不会复现）
- 太笼统的陈述（"优化了代码"、"改进了功能"、"重构了模块"）

---

## Timestamp — 每条 finding 必须带时间

```
ts: 这条知识是什么时候被验证/决定的。用 ISO 8601 格式。

**优先级：**
1. 对话消息 `timestamp` 字段 — 如果 finding 对应的讨论来自某条具体消息，用那条消息的时间
2. `[Snapshot saved at]` — 对话本身没有明确时间标记时，用快照保存时间
3. 对话内容推断 — 用户说了"上周"、"昨天"等，据此推算绝对时间

"ts" 决定了知识的新旧顺序。同一话题下，新的覆盖旧的，请尽量准确。
```

`pinned` 标记重要事项（每轮必看），`recent` 标记最新进展（用于 Recent changes 区）。两者独立。

如果没有任何值得记录的内容，返回 `"findings": []`。

## 约束

- 无必要不记录——宁可漏记，不要制造噪音
- LLM 训练数据中已有的通用知识 → 不记
- 不记的话 LLM 下次不会走同样弯路 → 不记
- 每条 content 必须脱离上下文也能读懂，包含触发条件和具体方案
- 没有模糊评价（"很好"、"不太好"）
- pinned 只用于每轮都需要看到的重要事项
- recent 只反映项目里程碑，不是静态知识沉淀
