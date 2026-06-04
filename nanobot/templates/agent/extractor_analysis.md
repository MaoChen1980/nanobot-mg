You are analyzing a conversation snapshot. The snapshot has two parts: system prompt
(what was already known) and conversation history (what happened).

**最高原则：宁缺毋滥。高价值信息优先。**

高价值信息的定义：**能持续连续影响后面 15 个 iteration，甚至将来长时间对判断和行为有影响的内容。**

判断标准（一条不符合就跳过）：
1. **影响持久** — 这条知识在 15 轮 iteration 后仍然有指导意义？还是下一轮就过期？
2. **改变行为** — 知道它和不知道它，AI 的决策会不一样吗？

反之，以下内容**不值得提取**：
- 太笼统（"优化了代码"、"改进了功能"）
- 只对当前 conversation 有用，下一轮就无关的

你的任务不是分类，而是**提炼**——把经验提炼成下次可以直接用的知识。

---

## session_summary — 会话摘要（重要）

用一句话概括本次 session 做了什么。这是 MEMORY.md "Recent changes" 的入口，负责工作连续性。

**格式：** `模块: 做了什么 — 关键技术/决策`

**规则：**
- 20-50 字，必须包含具体模块名和技术手段
- 不写 vague 描述（"修复了一些问题"、"做了一些改进"）
- 说清楚改了什么 + 怎么改的

**好：**
- `subagent_prompt: 语义搜索 team_board 注入 — keyword overlap 评分 + 自动追加`
- `memory_extractor: pinned 截断修复 — _trim_sentence 按句号边界截断`
- `openai_compat: Qwen 2.5 image 支持 — extract_block 识别 image/* Content-Type`
- `runner_retry: subagent 30s 轮询不再调 message() 刷屏`

**差：**
- `修改了一些文件`（没说是哪些、为什么）
- `改进了 memory_extractor`（太宽泛）
- `修复 bug`（没说什么 bug）

---

## Output Overview

提取两类信息：**facts**（事实）和 **verified behavior outcomes**（已验证的行为结果）。

用以下 JSON 格式输出。**每条 content 必须自成一体、包含触发条件 + 具体方案。**

---

## 1. Knowledge — Project Facts and Decisions

**客观事实：** 架构选择、配置约定、为什么是当前状态。这些是用户说过的陈述或团队达成的决策。

**content 质量要求：**
- 自包含：脱离上下文也能读懂
- 具体：包含技术细节（方法名、参数值、路径等）
- 精确：避免模糊评价（"很好"、"不太好"）

**好：**
- `Nanobot MemoryExtractor 使用 FAISS 做向量搜索，min_score 默认 0.3，用于 supersedes 查找`
- `Boss timer prompt 有三种决策分支：检查状态 / 取消空转 / 综合交付`
- `team_board.md 按 ## 标题分 section，keyword overlap 选 top-3 注入 subagent prompt`

**差：**
- `FAISS 用于搜索`（太笼统，谁用？在哪用？参数？）
- `架构上做了优化`（没说什么优化、为什么）
- `需要注意 tokenize 的问题`（具体什么问题？怎么解决？）

不要提取没有证据支持的观点、猜测或主观评价。

---

## 2. Behavior Outcomes — What Actually Happened

不要判断对话内容是否"有用"——仅凭聊天记录无法判断。
相反，要看 **tool execution results**（工具执行结果）。这是唯一可验证的信号。

| Signal | What to record |
|--------|----------------|
| 工具成功，产出了有用的输出，多步骤工作流 | `skill` — 可复用的多步骤工作流或避雷指南，值得保存为正式 skill |
| 工具成功，产出了有用的输出，单次技术操作 | `pattern` — 经过验证的有效路径（还不足以成为独立 skill） |
| 工具失败或输出错误结果 | `pitfall` — 一次失误，不要重蹈覆辙 |
| 走了一大段弯路后发现的捷径 | `skill` — 弯路不是 skill，但弯路尽头找到的简单方案是 skill |

**content 质量要求（同上）：**
- 自包含+具体+精确
- pitfall 必须包含：**什么操作导致失败 + 正确做法是什么**
- pattern 必须包含：**什么场景下适用 + 具体步骤或关键参数**
- 所有 content 保持一段话，不要拆成列表

**好（pitfall）：**
- `⚠️ _tokenize() 用 [a-zA-Z_] 不捕获中文 — team_board 内容需保留英文关键词或额外分词处理`
- `⚠️ read_file 在同一 session 内重复调用返回 '[File unchanged]' 而非内容 — 改用 Bash type/Get-Content 强制重读`

**差（pitfall）：**
- `⚠️ tokenize 中文有问题`（没说什么场景、怎么触发、怎么修）

**好（pattern）：**
- `💡 在 Windows 上用 Select-String 替代 grep：Get-ChildItem -Recurse -Filter *.py | Select-String -Pattern "keyword"`
- `💡 CronCreate 自循环监控模式：spawn → CronCreate → fire → check → CronCreate again → done → stop`

**差（pattern）：**
- `💡 可以用 Select-String 搜索`（什么时候用？怎么用？）

---

## Skill Criteria — Shortcut Pattern (捷径)

Skill 是一条**捷径**。它的价值是：以后遇到相同场景，不需要重新摸索，直接走这条。

两条发现途径：
1. **直接走通的** — 成功执行过的多步骤工作流，按步骤走就行
2. **绕路后发现的** — 走了一大段弯路，最后发现一个简单方案。弯路本身不是 skill，但弯路尽头找到的 insight 是

以下条件**全部**满足时才标记为 `skill`：
1. **重复 2+ 次**：同一工作流或同一陷阱在对话历史中出现过 2 次或以上
2. **有明确信号**：有可识别的触发条件（不是似是而非的"小心"）
3. **非显而易见**：没有这个 skill，agent 不会自然做对或会走弯路

一条记录有价值但不符合 skill 标准，用 `pattern`（单次技术操作）或 `knowledge`（事实性知识）。

---

## 3. Preference — User Preferences

用户**跨项目稳定**的工作方式和沟通偏好。仅当以下条件**全部**满足才记录：

1. **跨项目** — 换一个完全不同项目仍然成立（语言偏好、工作方式偏好），而非当前项目的特定约束
2. **稳定** — 不是一次性技术决策，不会在下个 session 失效
3. **用户明确表达或一致表现出** — 不是推测

**不记录：**
- 项目硬约束（"不要碰 ProcessManager"、"不要改 build.gradle"）→ 项目 memory 里
- 具体任务格式要求（"报告必须 ≤300 字"）→ 当前任务约束，不是用户偏好
- 一次性技术决策（"APK 可以到 300MB"、"Python 打到 shell-binaries.zip"）→ 当时上下文内的方案选择
- 同一个偏好不要重复记录。如果已经有一条"prefers concise Chinese"，再出现同样的内容直接跳过。
- 项目名/项目专属路径不出现在 preference 内容中。如果出现则说明不够跨项目。

**好的例子：**
- `User communicates in Chinese, prefers short concise replies`
- `User wants the agent to implement fixes autonomously rather than propose them`
- `User prefers data-driven decisions — let real usage data accumulate before investing in new features`

**差的例子：**
- `User wants mobile-ai-agent Shell to mirror nanobot-mg`（项目特定，不是跨项目偏好）
- `APK ≤ 300MB 用户可接受`（一次性技术决策）
- `User wants cmp-* reports capped at 300 words`（任务格式约束）

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

## Topic Naming — 主题命名规范

使用宽泛、稳定的 topic 名称，使相关内容积累在同一文件。

**规则：**
- 好（宽泛，能积累）：`Project/nanobot`, `AI/harness-design`, `Python/async`
- 差（太窄）：`Project/nanobot-db-schema-fix`
- 差（碎片化）：`Android/apk-analysis` 和 `Android/assets` 和 `Android/gradle` 各自独立 → 应合并为 `Android/build`
- 如果一个 topic 目录下已有超过 2 个小知识点，合并到同一文件，而不是各开新文件

---

## What NOT to Record

- 一次性命令，无可复用的洞见
- 琐碎的交互、问候、无关闲聊

---

## Output Format

```json
{
  "session_summary": "<20-50 字工作摘要：模块: 做了什么 — 关键技术>",
  "findings": [
    {
      "type": "knowledge|pitfall|pattern|preference|skill",
      "content": "<自包含、具体、包含触发条件+方案的一句话>",
      "topic": "<broad topic path>",
      "name": "<kebab-case name — required for pattern and skill>",
      "pinned": true,
      "supersedes": "<text describing old content to replace, or omit>"
    }
  ]
}
```

`pattern` 和 `skill` 必须提供 `name`。

**最后检查清单：**
- [ ] 每条 content 脱离上下文也能读懂吗？
- [ ] 包含了触发条件和具体方案吗？
- [ ] 没有模糊评价（"很好"、"不太好"）？
- [ ] session_summary 包含模块名和技术手段？
- [ ] pinned 真正重要到每轮都需要看到？

如果没有任何值得记录的内容，返回 `"findings": []`。
