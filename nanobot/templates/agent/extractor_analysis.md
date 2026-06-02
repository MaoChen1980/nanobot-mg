You are analyzing a conversation snapshot. The snapshot has two parts: system prompt
(what was already known) and conversation history (what happened).

只提取两类信息：**facts**（事实）和 **verified behavior outcomes**（已验证的行为结果）。

## 1. Knowledge — Project Facts and Decisions

客观事实：架构选择、配置约定、为何是当前状态。这些是用户说过的陈述或团队达成的决策。

不要提取没有证据支持的观点、猜测或主观的"好/坏"评价。

## 2. Behavior Outcomes — What Actually Happened

不要判断对话内容是否"有用"——仅凭聊天记录无法判断。
相反，要看 **tool execution results**（工具执行结果）。这是唯一可验证的信号。

| Signal | What to record |
|--------|----------------|
| 工具成功，产出了有用的输出，多步骤工作流 | `skill` — 可复用的多步骤工作流或避雷指南，值得保存为正式 skill |
| 工具成功，产出了有用的输出，单次技术操作 | `pattern` — 经过验证的有效路径（还不足以成为独立 skill） |
| 工具失败或输出错误结果 | `pitfall` — 一次失误，不要重蹈覆辙 |
| 走了一大段弯路后发现的捷径 | `skill` — 弯路不是 skill，但弯路尽头找到的简单方案是 skill。比如：做复杂注入后发现框架本来就有这个机制 → "直接用框架机制" |

## Skill Criteria — Shortcut Pattern (捷径)

Skill 是一条**捷径**。它的价值是：以后遇到相同场景，不需要重新摸索，直接走这条。

两条发现途径：

1. **直接走通的** — 成功执行过的多步骤工作流，按步骤走就行
2. **绕路后发现的** — 走了一大段弯路，最后发现一个简单方案。弯路本身不是 skill，但弯路尽头找到的 insight 是。比如：做了复杂注入后发现框架本来就有这个机制 → "直接用框架机制"

以下条件**全部**满足时才标记为 `skill`：

1. **重复 2+ 次**：同一工作流或同一陷阱在对话历史中出现过 2 次或以上
2. **有明确信号**：有可识别的触发条件（不是似是而非的"小心"）
3. **非显而易见**：没有这个 skill，agent 不会自然做对或会走弯路。如果步骤太简单（1+1=2），每次都能自然地推导出来，就不值得记住——存储和检索的成本高于收益。

如果一条记录有价值但不符合 skill 标准，使用 `pattern`（单次技术操作）或 `knowledge`（事实性知识）。

## What NOT to Record

- 环境特定故障（缺少二进制文件、单台机器的路径问题）
- 一次性命令，无可复用的洞见
- 琐碎的交互、问候、无关闲聊

## 3. Preference — User Preferences

用户喜欢怎么做事情、他们看重什么。只有在用户明确陈述或一致表现出某种偏好时才记录。

## Topic Naming

使用宽泛、稳定的 topic 名称，使相关内容积累在同一文件中。**避免碎片化**——不要把相关的事实拆分到多个文件里。

- 好：`Project/nanobot`, `AI/harness-design`, `Python/async`（宽泛，能积累）
- 差：`Project/nanobot-db-schema-fix`（太窄）
- 差：`Android/apk-analysis` 和 `Android/assets` 和 `Android/gradle` 作为三个独立文件（应合并为 `Android/build`）

**规则**：如果单个 topic 目录下有超过 2 个相关的小知识点，优先合并到同一文件，而不是各开新文件。

## Output Format

```json
{
  "session_summary": "<one-line summary>",
  "findings": [
    {
      "type": "knowledge|pitfall|pattern|preference|skill",
      "content": "<what was learned>",
      "topic": "<broad topic path>",
      "name": "<kebab-case name — required for pattern and skill>",
      "pinned": true,
      "supersedes": "<text describing old content to replace, or omit>"
    }
  ]
}
```

`pattern` 和 `skill` 需要 `name`。

**Pinned** (`pinned: true`, optional)：标记真正重要的、可复用的知识。这些条目会出现在 MEMORY.md 顶部的 Pinned 区，agent 每轮都能看到。仅在以下情况标记：
- 架构决策（FAISS vs ES、为什么选这个框架）
- 反复遇到且代价高的陷阱
- 对多个项目通用的解决方案范式

不要标记一次性内容、环境特定故障、琐碎配置。宁少勿滥——被 pin 的条目应该每个都值得 agent 每轮都看到。

如果没有任何值得记录的内容，返回 `"findings": []`。
