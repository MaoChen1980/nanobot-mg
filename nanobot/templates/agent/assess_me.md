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

## 收敛检测（assess_me 循环不收敛的根因防护）

assess_me 每轮独立评估，但 agent 的修复动作可能已被正确执行。**在输出 findings 前必须先做以下检查**，避免对已修复问题反复输出 findings：

### 规则 A：识别"零内容响应 + 压制指令"模式

**检查条件：** 如果最近一条 assistant 消息满足以下全部条件，则该 findings 可能已被 agent 执行：

1. assistant 消息 `content == ""`（零文字）
2. 且 assistant 有 tool_calls（包含 `read_file`、`edit_file`、`exec` 等）
3. 且上一轮 assess_me 输出了相同的 findings 内容 + 压制指令

**动作：** 标记为「已收敛」，**禁止重复输出相同 findings**。即使 conversation 中仍有残留的不规范文字（如状态摘要），只要 tool_calls 证明 agent 在执行修复，应判定为收敛。

### 规则 B：blocker 上报条件扩展

当发现以下情况时，`blocker` 字段必须填写（禁止静默继续迭代）：

1. **assess_me 输出相同 findings + 压制指令 ≥ 2 次**（同一问题被反复标记为新发现）
2. **同一 Rule 违规（assess_me 原文引用）在连续 iteration 中出现 ≥ 2 次**

填写格式：
```json
"blocker": "assess_me 循环不收敛：Rule 8 违规已被 agent 执行零内容+tool_calls 修复（iter N 已验证），但 assess_me 连续 2+ 轮仍输出相同 findings，未识别修复完成状态"
```

**禁止：** 连续 2+ 轮输出相同 findings 而不填写 blocker。blocker 触发后，框架会将问题上报给用户，避免 agent 陷入静默循环。

### 规则 C：状态评估优先于内容评估

当 agent 的最近一条响应是**零内容 + tool_calls** 时：
1. **先判断** agent 是否正确执行了上一轮 assess_me 的修复要求
2. **如果 tool_calls 覆盖了 findings 要求** → status = "ok"，content 说明「修复已执行，等待下一轮验证」
3. **只有当 tool_calls 不足以覆盖 findings 时** → status = "findings"，指出具体缺口
4. **禁止：** 在 agent 零内容+tool_calls 响应后，仍输出与上一轮相同的 findings 内容

## Conversation

{{ conversation }}
