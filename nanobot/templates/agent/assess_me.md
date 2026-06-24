评估最近一条 assistant 回复的逻辑推理合理性、论据充分性，识别 blocker 和可复用 skill。输出 JSON 格式。

{
  "status": "ok" | "findings",       // ok=正常  findings=发现问题
  "summary": "一句话总结",
  "need_drc": false,                  // true=同一问题尝试3次仍未解决, 需深挖根因
  "blocker": null | "当前遇到的阻塞问题描述",
  "skill_pattern": null | "可复用模式(做什么+不做什么)",
  "needs_revision": false,            // true=最近一条 assistant 回复不准确/论据不足/逻辑不成立, 需修正
  "content": "详细分析(markdown)"
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


---

{% if has_active_task %}

### 流程层 — agent 的推进状态


#### 2. 任务完成评估
首先从对话历史中识别出：**用户的原始任务/请求是什么？** 然后判断：
- **完成没有？** — 输出不达标，任务没有完成，agent 应该继续
- **偏离任务** — agent 在做与原始任务无关的事情，需要纠正
- **阻塞 blocker** — 遇到无法解决的问题，需要返回给用户说明

#### 3. 假设检查
当前决策所依赖的、尚未被验证的假设：
- ✅ 可通过工具调用验证的
- ❌ 已被矛盾证据质疑的

{% endif %}

### 信息缺口

覆盖内容层和流程层两个维度：
- **陈述层面**：最近一条 assistant 回复是否引用了未获取或未验证的信息
- **任务层面**：整体任务还需要哪些信息才能继续推进

如果信息可以通过工具获取，说明需要什么工具和查询目标。


#### 可复用模式 — 进化门控

仅分析最后 20 次 iteration 内的行为。**目标是让 agent 进化，不是记录对话中做过的事。**

**信息来源必须是外部输入，只可能来自以下三种之一：**
- 踩坑修复（尝试→失败→排查→修复）
- 绕路后发现捷径（走通了但绕远路，发现了更快的路径）
- 用户纠正/提示（用户明确说「不对」「应该这样」）

不是这三种来源产生的 → 不是 skill，不写。

**以下任一条件满足即可，满足越多越好：**

1. **进化增量** — 没这个 skill，下次 LLM 表现明显更差（绕更多路、犯同样错误）。没区别就不写。
2. **不可推理** — 这个 pattern 从第一原理推不出来，或要 3+ 轮尝试才试对。标准做法不写。
3. **有失败细节** — 不只写「做什么」，还写「不做什么」「哪里会失败」「为什么这个方式 work」。

**噪音的特征：** 读完后觉得「本来就该这么干」→ 不写。
**进化的特征：** 读完后觉得「原来有个坑 / 原来可以这样」→ 写。

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
- 有阻塞则写 `blocker`；同一 blocker 反复 3 次未解决则额外 `need_drc: true`

## Conversation

{{ conversation }}
