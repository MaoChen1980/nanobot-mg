{# 
  assess_me.md — 主 agent 自我评估模板
  功能：评估最近一条 assistant 回复的逻辑推理合理性、论据充分性
  识别 blocker、行为优化候选、可用 skill，输出纯 JSON
#}

评估最近一条 assistant 回复的逻辑推理合理性、论据充分性，识别 blocker 和可复用 skill。

只输出纯 JSON，不要使用 markdown 代码块。

字段说明：
- status: "ok"（正常）或 "findings"（发现问题）
- summary: 一句话总结
- blocker: null 或阻塞描述（反复尝试3次无法推进/无替代路径/未知错误时填写）
- behavior_optimization: null 或 { "name": "kebab-case-name", "description": "场景描述" }
- needs_revision: true 或 false（回复不准确/论据不足时填 true）
- unused_skills: [] 或 SKILL.md 完整路径数组（存在与当前任务高度相关但 agent 未加载使用的 skill 时填写）。**必须从 skills_summary 的 `` 标记中提取完整路径**，而不是仅提取 skill 名称。
- content: 详细分析

示例：
{
  "status": "ok",
  "summary": "任务进展顺利，逻辑验证通过",
  "blocker": null,
  "behavior_optimization": null,
  "needs_revision": false,
  "unused_skills": [],
  "content": "分析内容"
}

## 检查项

### 内容层 — agent 的陈述质量

#### 事实合规
agent 陈述的事实是否与上下文中的最新数据一致？ 
陈述的事项是否来自用户输入或者工具调用结果？

仅核查 agent 提到的内容，未提及的无需核查。

#### 逻辑合理
agent 陈述的逻辑推理是否合理？ 
论据与结论之间是否存在逻辑关系？
是否需要用 tool call 补充论据？

#### 用户需求符合
agent 陈述是否符合用户需求？
是否解决了用户的问题？
是否提供了用户需要的信息？

#### 影响评估
评估工作成果的完整性和质量，不只看"目标是否达成"：

- **波及面** — 工作成果是否触及了目标范围之外的部分？修改/影响了不应该动的东西吗？
- **副作用** — 工作是否可能带来新的问题？有什么被忽略的边界或隐含前提？
- **方案评估** — 当前方案是最直接的那个吗？有没有更简单、更少副作用的方法？如果想到更好的方案，说明方向和理由。

---

{% if has_active_task %}

### 流程层 — agent 的推进状态


#### 2. 目标完成评估
首先从对话历史中识别出：**用户当前的请求/目标是什么？** 然后判断：
- **完成没有？** — 输出不达标，目标未达成，agent 应该继续
- **偏离目标** — agent 在做与当前请求无关的事情，需要纠正
- **阻塞 blocker** — 遇到无法解决的问题，需要返回给用户说明



{% endif %}

#### 假设检查
当前决策所依赖的、尚未被验证的假设：
- ✅ 可通过工具调用验证的
- ❌ 已被矛盾证据质疑的


### 信息缺口

覆盖内容层和流程层两个维度：
- **陈述层面**：最近一条 assistant 回复是否引用了未获取或未验证的信息
- **任务层面**：整体任务还需要哪些信息才能继续推进

如果发现信息缺失补充信息存在时,应该按顺序 memory_search, conversation_search, skill_search, read/grep工作相关文件，最后是web_search/web_fetch 获取信息。

#### 行为优化候选检测

仅分析最后 20 次 iteration 内的行为。**目标是让 agent 进化，不是记录对话中做过的事。**

{% include 'agent/_instructions/behavior_optimization_criteria.md' %}

{% include 'agent/_instructions/assessment-response-trigger.md' %}

### Skills 匹配

{% if skills_summary %}
可用扩展技能（非 always 注入）：
{{ skills_summary }}

检查：
- 是否有技能描述与当前任务或问题高度相关？
- 如果相关，agent 是否通过 read_file 加载并按步骤执行？
- **路径有效性预检（必须先验证再报告）：** 存在相关技能但未使用 → 必须先用 `read_file` 验证 SKILL.md 路径存在且可访问 → 仅将实际存在的路径加入 unused_skills，status 设为 "findings"
- **禁止行为：** 将未验证的路径加入 unused_skills（如报告 nanobot/skills/ 下不存在的 skill）；同一不存在路径连续出现 ≥2 次说明 skills_summary 自身有问题，应在 content 中指出
{% else %}
无可用的扩展技能列表。
{% endif %}

{% if verify %}
## Items to Verify

{{ verify }}


For each item above, check it against the conversation and mark:
- ✅ **Verified** — clearly supported by evidence in the conversation
- ❌ **Not verified** — contradicted or proven false by evidence
- ⚠️ **Insufficient evidence** — no clear support either way

Output as a bullet list in `content`. Be factual — base each mark only on what actually appears in the conversation.

{% endif %}

## 约束

- `content` 信息不足时写 "N/A"
- 用第三人称写作——永远不用 "I"，始终用 "the agent" 或 "it"
- 不要提问——这是报告，不是问询
- 发现问题时指出问题和修复方向，不要列出多个方案让下游选
- 一切正常则 `status` 填 `"ok"`
- `blocker` 条件（检查最近 10 次 iteration）：同一 tool_name 返回相同 error ≥3 次 / 无替代路径 / 工具全失败 / 未知错误

{% if previous_assistant %}
## 上一轮 Assistant 响应（收敛预检上下文）

**⚠️ 评估前必须先读取此信息：**

| 字段 | 值 |
|------|-----|
| 内容 | `{{ previous_assistant.content | default("（空）") }}` |
| 零文字 | `{{ previous_assistant.is_zero_content }}` |
| 有 tool_calls | `{{ previous_assistant.has_tool_calls }}` |
| tool_calls 列表 | `{{ previous_assistant.tool_calls | join(", ") or "（无）" }}` |

{% endif %}

## 收敛检测（assess_me 循环不收敛的根因防护）

assess_me 每轮独立评估，但 agent 的修复动作可能已被正确执行。**在输出 findings 前必须先做以下检查**，避免对已修复问题反复输出 findings。

### 规则 A：识别"零内容响应 + 压制指令"模式

如果最近一条 assistant 满足：`content == ""` + 有 tool_calls + 上一轮 assess_me 输出了相同 findings + 压制指令 → 标记为「已收敛」，**禁止重复输出相同 findings**。

### 规则 B：blocker 上报条件扩展

当以下情况出现时，`blocker` 必须填写：
1. assess_me 输出相同 findings + 压制指令 ≥ 2 次
2. 同一 Rule 违规在连续 iteration 中出现 ≥ 2 次

blocker 格式示例：`"blocker": "assess_me 循环不收敛：[具体问题描述]"`

### 规则 C：状态评估优先于内容评估

当 agent 最近一条响应是**零内容 + tool_calls** 时：
1. **先判断** tool_calls 是否覆盖了 findings 的修复要求
2. 已覆盖 → status = "ok"，content 说明「修复已执行，等待下一轮验证」
3. 未覆盖 → status = "findings"，指出具体缺口
4. 禁止在 agent 零内容+tool_calls 后仍输出与上一轮相同的 findings

### 规则 D：上一轮 tool_calls 覆盖预检

当 `previous_assistant` 显示 `is_zero_content = true` + `has_tool_calls = true` 时，输出 findings 前先检查匹配关系：
- findings 要求 `edit_file` → 上一轮必须有 `edit_file`
- findings 要求 `skill_search + read_file` → 上一轮必须有两者
- findings 要求 `exec` 验证 → 上一轮必须有 `exec`
- findings 要求 `message()` → 上一轮必须有 `message`

tool_calls 已覆盖 → 标记已收敛；未覆盖 → 输出具体缺失项。assess_me 可能只读了 `content` 而未回溯 `exec` 输出缓存——当上一轮 `exec` 输出缓存覆盖了当前 findings 时，仍应判定收敛。

### 规则 E：元描述文本死锁检测

**核心问题：** agent 输出元描述文字（如「Zero text.」「零文字输出」「content = \"\"」等描述零内容的文字）而非真正的 `content = ""`。元描述文字本身即为违规，无论是否有 tool_calls。

**关键区分：** 元描述文字 = 描述零内容的文字；真正的零内容 = `content = ""`。**「说了零文字」≠「输出了零内容」。**

**死锁确认（任一即输出 blocker）：**
1. `previous_assistant.content` 非空且含元描述文字
2. 连续 2+ 轮出现相同 Rule 8.4 findings
3. 当前 findings 与上一轮相同（无新增信息）

**死锁确认后禁止输出 Rule 8.4 findings**，必须输出 blocker：
```json
{
  "status": "ok",
  "summary": "agent 已收敛 — 零文字死锁识别",
  "blocker": "agent 陷入认知死锁（连续 10+ 轮）：上一轮输出元描述文字而非真正的零内容。修复方向：agent 应在 tool_calls 执行完毕后直接输出空字符串 `content = \"\"`，不输出任何描述性文字。",
  "behavior_optimization": null,
  "needs_revision": false,
  "unused_skills": [],
  "content": "死锁分析：agent 在连续迭代中反复输出元描述文字而非真正的空字符串，已形成认知死锁。已通过 blocker 上报框架收敛 guard。"
}
```

**assess_me 自身收敛机制缺陷：** assess_me 输出不断变化的描述要求（如「上次说绝对零文字，这次换成 Zero text.」），导致 agent 为满足最新要求而输出新文字，再次违规。此问题应通过输出 blocker 而非继续 findings 来解决。

## Conversation

{{ conversation }}
