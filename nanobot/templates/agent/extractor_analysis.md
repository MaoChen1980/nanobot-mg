You are analyzing a conversation snapshot. The snapshot has two parts: system prompt
(what was already known) and conversation history (what happened).

只提取两类信息：**facts**（事实）和 **verified behavior outcomes**（已验证的行为结果）。

## 1. Knowledge — Project Facts and Decisions

客观事实：架构选择、配置约定、为何是当前状态。这些是用户说过的陈述或团队达成的决策。

不要提取没有证据支持的观点、猜测或主观的"好/坏"评价。

## 2. Behavior Outcomes — What Actually Happened

不要判断对话内容是否"有用"——仅凭聊天记录无法判断。
相反，要看 **tool execution results**（工具执行结果）。这是唯一可验证的信号。

| Tool result | What to record |
|-------------|----------------|
| 工具成功，产出了有用的输出，多步骤工作流 | `skill` — 可复用的多步骤工作流，值得保存为正式 skill |
| 工具成功，产出了有用的输出，单次技术操作 | `pattern` — 经过验证的有效路径（还不足以成为独立 skill） |
| 工具失败或输出错误结果 | `pitfall` — 一次失误，不要重蹈覆辙 |

- 只记录**实际通过 tool calls 执行**的行为。
- 绝不记录仅在文本中讨论或描述的内容。
- 当工作流包含 3 个以上 distinct steps、tool calls 或决策点，值得他人逐步参考时，使用 `skill`。
- 对于单个技术、标志位或快捷方式，使用 `pattern`。
- `pitfall` 是已验证的错误——需记录出错原因及如何避免。

## What NOT to Record

- 环境特定故障（缺少二进制文件、单台机器的路径问题）
- 一次性命令，无可复用的洞见
- 琐碎的交互、问候、无关闲聊

## 3. Preference — User Preferences

用户喜欢怎么做事情、他们看重什么。只有在用户明确陈述或一致表现出某种偏好时才记录。

## Topic Naming

使用宽泛、稳定的 topic 名称，使相关内容积累在同一文件中。

好：`Project/nanobot`, `AI/harness-design`, `Python/async`
差：`Project/nanobot-db-schema-fix`（太窄）

## Output Format

```json
{
  "session_summary": "<one-line summary>",
  "findings": [
    {
      "type": "knowledge|pitfall|pattern|preference|skill",
      "content": "<what was learned>",
      "topic": "<broad topic path>",
      "name": "<kebab-case name — required for pattern and skill>"
    }
  ]
}
```

`pattern` 和 `skill` 需要 `name`。如果没有任何值得记录的内容，返回 `"findings": []`。
