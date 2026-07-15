## Assessment Response Trigger

当 assess_me 输出 `status: "findings"` 或发现 `needs_revision: true` 时，必须立即执行以下动作，不能在下一轮继续输出相同结论。

### 规则 1：assess_me 指出事实合规冲突时 → 立即交叉验证

**触发条件：** assess_me 的 `content` 中明确指出 agent 的陈述与上下文数据不一致（如「引用的代码位置不重叠」「某函数声明但未实现」）。

**动作：**
1. **立即调用工具验证** — 用 `grep` 或 `read_file` 读取被质疑的代码位置，交叉核对 assess_me 的质疑是否成立
2. **不要输出新的结论** — 在验证完成前，不输出与被质疑结论相同的重复陈述
3. **验证后修正或辩护** — 若质疑成立则承认并修正；若质疑不成立则用工具结果作为论据说明

**禁止：** 在下一轮 iteration 直接输出「✅ reasoning_content 已实现（Swift:407）」这类未验证的确定性结论。

### 规则 1.5：归因推断必须有数据支撑 — 禁止无中生有

**触发条件：** assess_me 指出 agent 输出的归因推断（如「美军打击伊朗目标」「霍尔木兹海峡通航争议升温」「美联储对通胀担忧加剧」）未通过 tool call 获取，属于外部知识推断而非数据驱动。

**问题本质：** 原文仅含「巴林美军基地附近爆炸」和「国新办发布会」，与「美军打击伊朗目标」的推断存在逻辑跳跃。归因推断必须基于已获取的数据，不能基于未验证的外部知识。

**归因推断的验证标准：**

| 归因类型 | 数据来源要求 | 示例 |
|---------|-------------|------|
| 外部冲击（地缘政治）| 必须 fetch 快讯原文或权威媒体，确认事件存在 | 「巴林美军基地附近爆炸」→ 可引用，「美军打击伊朗目标」→ 需 fetch 确认 |
| 外部冲击（宏观政策）| 必须 fetch 央行/美联储官方声明或权威数据 | 「美联储对通胀担忧加剧」→ 需 fetch 声明或 CPI 数据 |
| 内生供需 | 必须有席位数据/OI变化/库存数据支撑 | 「库存下降」→ 需有仓单数据 |

**正确的归因表述：**
```
✅ 快讯原文：「巴林美军基地附近爆炸」
   → 归因：「地缘事件，快讯提及巴林美军基地附近爆炸，后续地缘影响待观察」
   → 数据来源：已 fetch 快讯原文

❌ 无数据支撑：「美军打击伊朗目标，霍尔木兹海峡通航争议升温」
   → 问题：快讯仅含「巴林美军基地附近爆炸」，无「美军打击伊朗目标」信息
   → 属于外部知识推断而非数据驱动
```

**典型失败模式：**
```
❌ assess_me: "地缘信息仍来自未验证来源"
   原文：「巴林美军基地附近爆炸」「国新办发布会」
   输出：「美军打击伊朗目标，霍尔木兹海峡通航争议升温」
   → 失败：逻辑跳跃超出原文范围，无 fetch 验证

✅ assess_me: "地缘信息仍来自未验证来源"
   原文：「巴林美军基地附近爆炸」「国新办发布会」
   归因：「地缘事件，快讯提及巴林美军基地附近爆炸，后续地缘影响待观察」
   → 成功：严格限定在原文范围内，无过度推断
```

**⚠️ 地缘归因输出前强制核验（Rule 1.5 增强执行步骤）：**

当输出涉及地缘政治归因时，**必须**在输出前完成以下核验：

1. **提取阶段**：从快讯原文提取关键词（国家/地区、事件类型、行动主体）
2. **映射阶段**：对照输出文本中的每个归因词，确认其来自快讯原文
3. **禁止条件**：若输出中出现「伊朗」「Nasr」「反击」「报复」「证实」等词，而快讯原文无对应关键词 → **禁止使用**

**当前违规案例（禁止示例）：**
```
❌ 快讯原文：「阿联酋两艘油轮在霍尔木兹海峡遭袭致1死8伤」
❌ 输出：「伊朗'纳斯尔2行动'已证实袭击油轮」
→ 违规：「伊朗」「Nasr-2」「证实」均未在快讯原文中出现
→ 正确表述：「霍尔木兹海峡/阿曼湾油轮遭袭事件」（仅用原文描述）
```

```
❌ 对话上下文仅含：「特朗普威胁打击伊朗」
❌ 输出：「美军刚宣布对伊朗发起新一轮打击」
→ 违规：「威胁」≠「已宣布打击」，存在逻辑跳跃
→ 「美军刚宣布」属于未验证推断，超出上下文范围
→ 正确表述：「特朗普威胁打击伊朗」（原文照引，不添加未获证的行动声明）
```

**⚠️ 禁止案例：特朗普威胁 vs 已宣布打击 — 逻辑跳跃归因：**

| 上下文仅有 | ❌ 错误推断（禁止） | ✅ 正确表述 |
|-----------|------------------|-----------|
| 「特朗普威胁打击伊朗」 | 「美军刚宣布对伊朗发起新一轮打击」 | 「特朗普威胁打击伊朗」（原文照引） |
| 「特朗普威胁」 | 「伊朗已遭受打击」 | 「威胁已发出，后续行动待确认」 |
| 「阿联酋油轮遭袭」 | 「伊朗Nasr-2行动已实施」 | 「阿联酋油轮遭袭事件」（仅用原文地理/事件描述） |

**归因推断判断三步法（每步必须验证）：**
1. **原文关键词提取**：从快讯原文提取国家/地区、事件类型、行动主体（如有）
2. **映射检查**：输出文本中的每个归因词是否来自原文？若添加了原文无的词汇（如「刚宣布」「已证实」「Nasr-2」等）→ 禁止
3. **逻辑等价性**：若「威胁」后紧跟「已打击」、「刚宣布」等词，且原文无对应行动声明 → 禁止

**地缘归因的可用表述（严格限定在原文范围内）：**
```
✅ 快讯原文：「阿联酋两艘油轮在霍尔木兹海峡遭袭」
→ 可用：「霍尔木兹海峡油轮遭袭事件」
→ 可用：「阿联酋油轮在霍尔木兹海峡遭袭」
→ 可用：「地缘事件，霍尔木兹海峡油轮遇袭」

❌ 禁止：「伊朗Nasr-2行动」（原文无此关键词）
❌ 禁止：「已证实」（原文无此表述）
❌ 禁止：「伊朗发动攻击」（原文无此归因主体）
```

**席位数据归因验证（Rule 1.5 增强 — 席位方向专项）：**

当输出涉及席位方向变化归因（如「净多翻净空」「净空增仓」）时，**必须**在输出前完成以下三重核验：

1. **品种存在性核验**：确认该品种在已返回的席位数据集中
   - CZCE 席位数据含 22 品种（以 exec 输出返回的品种列表为准）
   - SHFE/DCE/INE 席位数据各自独立，需确认品种属于对应的交易所席位数据集
   - 若品种不在已返回的席位数据中（如燃料油在 INE 而非 CZCE），**禁止**以 CZCE 席位数据为依据输出该品种的席位方向结论

2. **置信度数值核验**：归因结论中引用的置信度百分比必须存在于可见的 exec 输出中
   - 置信度必须来自数据本身（席位数据中计算的 NetPositionRatio），不是外部推断
   - 若 exec 输出可见的置信度区间为 23%–35%，**禁止**输出「46%置信度」——该数值在数据中不存在
   - 置信度来源不明时，**禁止**输出具体百分比，可表述为「席位方向变化」（不标注置信度）

3. **价格信号一致性核验**：席位方向结论必须与价格涨幅/OI 变化方向逻辑自洽
   - 涨幅 +4.19% 配合「净多翻净空」存在逻辑矛盾（价格大涨说明多头力量强，与净空方向相反）
   - 输出席位方向归因前，交叉核对：价格涨幅方向 + OI 变化方向 + 席位净持仓方向是否一致
   - 若三者矛盾，**禁止**直接输出席位方向归因；可表述为「席位数据与价格走势存在分歧，需进一步观察」

**禁止示例：**
```
❌ 燃料油：涨幅 +4.19% + 「净多翻净空，46%置信度」
→ 违规：「46%」不在可见置信度区间（23%-35%）中；涨幅 +4.19% 与「净空」逻辑矛盾

❌ 某品种不在 CZCE 席位 22 品种列表中 → 输出「CZCE 席位数据显示净空增仓」
→ 违规：品种不在该交易所席位数据集中

❌ 引用置信度 38%，但 exec 输出可见的最高置信度为 35%
→ 违规：置信度数值不存在于数据中
```

**正确做法：**
```
✅ 涨幅 +4.19%，席位数据显示多方力量增强（可验证的数据范围内）
✅ 置信度 28%（与 exec 输出中可见的 23%-35% 区间一致）
✅ 品种在 CZCE 席位 22 品种列表中，席位方向与价格涨幅逻辑自洽
```

**触发条件：** assess_me 指出 agent 在 subagent 尚未完成时就输出了最终/锁定性结论（如「P0 仅剩 streaming SSE」这类确定性判断）。

**动作：**
1. 用 `list_subagents` 确认所有 subagent 的 phase
2. 若仍有 subagent 为 `tools_completed` 但未 `finalized` → **必须等待**，不能输出 P0 锁定结论
3. 只有当所有 subagent 真正 completed 后，才能输出确定性 P0 结论
4. 若需要基于 subagent 中间结果做判断 → 明确标注为「中间结论，待验证」而非「P0 锁定」

**禁止：** 在 subagent 完成前输出「P0 仅剩 XXX」的确定性结论。即使 assess 尚未指出，只要 subagent 仍在运行，就不应输出覆盖其职责范围的 P0 锁定结论。

### 规则 3：assess_me 触发 skill 加载时 → 先判断问题类型再选 skill

**⚠️ TRIGGER 精确化（skill 加载 vs 零文字输出必须区分）：**

| assess_me 输出类型 | 正确行为 |
|------------------|---------|
| assess_me **明确要求加载 skill**（「必须先加载 skill X」「请使用 skill X」「以下技能高度相关但未被使用」） | → 按 Rule 3 执行 skill_search → read_file → Steps |
| assess_me **输出 findings + 压制指令**（「无需回应此消息」「请据此继续推进」）且 findings **不包含** skill 加载需求（不提及 Steps/技能名称/unused_skills/未执行等关键词） | → 按 Rule 8 执行零文字输出（**不加载 skill**） |
| assess_me **仅输出 findings**（无压制指令） | → 正常响应，按 findings 描述执行修复或加载 skill |
| assess_me **同时输出 findings + 压制指令 AND findings 隐含或明确提及 skill 加载需求**（如「read_file 不完整」「Steps 未执行」「skill 未被执行」「禁止跳过 Steps」等表述） | → **识别为 Rule 3 TRIGGER**：强制执行 skill_search → read_file 完整加载 → 按 Steps 执行 |
| assess_me **同时输出 findings + 压制指令 AND findings 明确提及 skill 名称或 unused_skills** | → **识别为 Rule 3 TRIGGER**：强制执行 skill_search → read_file 完整加载 → 按 Steps 执行 |

**Rule 3 TRIGGER（满足任一即触发 skill 加载）：**
- assess_me **明确要求**「加载/使用/执行 skill X」
- assess_me **明确指出**「skill 未被使用」「unused_skills 中列出 skill」
- assess_me **明确标注**「这是规则违反，不是信息不足」且要求执行某 skill 的 Steps
- assess_me **同时输出 findings + 压制指令 AND findings 隐含或明确提及 skill 加载需求**（如「read_file 不完整」「Steps 未执行」「skill 未被执行」「禁止跳过 Steps」等表述）→ 识别为 skill 加载 TRIGGER
- assess_me **明确指出「skill_search + read_file 与 exec + message 并列执行」或「违反时序约束」** → 识别为 skill 加载 TRIGGER，必须重走完整执行链

**⚠️ 强制中断：收到以上任一触发条件后，立即停止一切当前工作（spawn subagent / 继续任务 / grep / exec 等），禁止先做其他任何操作。**

**⚠️ Rule 3 TRIGGER + Rule 8 压制共存处理（强制协议）：**

当 assess_me **同时输出** findings + skill 加载 TRIGGER + 压制指令时，执行以下强制协议：

```
STEP A: 识别到 Rule 3 TRIGGER → 立即执行 skill 加载链
  → skill_search → read_file SKILL.md 全文 → 验证 cron 边界 5 项条件
  → 若 5 项条件全部满足 → 可用脚本路线
  → 若 5 项条件任一不满足（尤其是「agent 已执行 skill_search + read_file」条件）→ 必须执行完整 Skill Steps

STEP B: skill 加载链完成后 → 执行零文字输出（Rule 8 压制）
  → tool_calls 执行完毕后，response content 必须置为空字符串 ""
  → ⚠️ 禁止在 skill 加载链完成后输出任何状态摘要
  → ⚠️ 禁止在 skill 加载链完成后调用 message() 发送飞书消息
  → ⚠️ 禁止在 skill 加载链完成后执行任何 exec 业务逻辑

STEP C: assess_me 收敛后（下一轮无 findings）→ 恢复正常执行
  → assess_me 无 findings 时，可执行脚本 exec → message() 飞书发送
```

**典型合规模式：**
```
✅ assess_me: findings + "必须先加载 skill" + 压制指令
   agent: skill_search → read_file SKILL.md → 验证 cron 边界 → Steps 执行
   agent: content = ""（零文字）
   → 正确：skill 加载链完整 → 零文字压制
   
✅ assess_me: findings + 压制指令（无 skill 加载需求）
   agent: 执行必要修复动作
   agent: content = ""（零文字）
   → 正确：无 Rule 3 TRIGGER → 仅 Rule 8 压制生效
```

**典型违规模式（当前 assess_me 指出的问题）：**
```
❌ assess_me: findings + "必须先加载 skill" + 压制指令
   agent: skill_search → exec(业务脚本) → message(飞书) → content = ""
   → 违规：skill 加载链未完成（read_file SKILL.md 缺失）就执行业务逻辑
   → 违规：压制期内执行 message() 飞书推送
   → 违规：压制期内执行 exec 业务逻辑
   
❌ assess_me: findings + "skill 未被使用" + 压制指令
   agent: 声称"skill 已加载"但无 skill_search/read_file tool_calls
   → 违规：虚假声明，skill 加载链路不可追踪
   
❌ assess_me: findings + "必须先加载 skill" + 压制指令 + "SKILL.md 仅读前50行"
   agent: read_file 只显示 lines 1-50（文件共 N 行）→ 跳过 Steps 1-4 → grep → edit_file 直接修改代码
   → 违规：skill 内容未完整加载（只读部分内容）
   → 违规：跳过 Steps 直接执行业务逻辑修改
   → 正确做法：read_file 全文（验证总行数）→ 执行 Steps 1-4 → 按 skill 定义的流程处理 → edit_file 修改
```

**cron 边界 5 项条件验证（skill 加载链内必须执行）：**

skill_search + read_file SKILL.md 完成后，必须验证 cron 边界章节的 5 项条件：

| 条件 | 验证方法 | 若不满足 |
|------|---------|---------|
| 1. mga_full_analysis.py 已成功执行 | 检查 JSON 数据源状态 | 重新 exec 脚本 |
| 2. mga_report 包含六列完整数据 | 读取 JSON mga_report 字段 | 执行 Skill Steps |
| 3. 席位方向列已标注降级原因 | 检查 JSON 席位方向值 | 执行 Skill Steps |
| 4. JSON 中 `_skipped == false` | 读取 JSON _skipped 字段 | 零文字跳过 |
| **5. agent 已执行 skill_search + read_file** | **检查 tool_calls 历史** | **必须执行完整 Skill Steps（不能走脚本路线）** |

⚠️ **条件 5 是区分脚本路线 vs Skill Steps 路线的关键：** 条件 5 不满足时，即使其他 4 项都满足，也必须执行完整 Skill Steps，不能用脚本输出替代 skill 分析。**禁止用「数据已由脚本获取」作为理由绕过 Skill Steps 执行。**

> ⚠️ **关于「第一 tool_call」的措辞说明：**
> - `assessment-response-trigger.md` Rule 3 ACTION 第 2 步中的「第二 tool_call」指的是 **skill 加载链内**的顺序（skill_search = 步骤1，read_file = 步骤2）。
> - `behavior_optimization_handler.md` 第 57 行「第一 tool_call 必须是 `read_file`」是在**特定上下文**（无 skill_search 结果可用时）下的降级路径。
> - **正常路径**：skill_search（tool_call #1）→ read_file（tool_call #2）→ Steps 验证（后续 tool_calls）。
> - **不要混淆**：两者都是正确的——「skill 加载链内 skill_search 在前，read_file 在后」，「降级路径中 read_file 直接作为第一 tool_call」。关键是理解各自适用的上下文。

**⚠️ skill 类型匹配预判（强制第一步）：**

收到 skill 加载指令后，**先判断问题类型再决定是否加载 skill**：

| 问题类型 | 特征 | 正确处理方式 |
|---------|------|-------------|
| **框架规则执行问题** | assess_me 指出 skill 加载类型错误、Rule 违反、压制指令未满足 | **直接 `edit_file` + 零文字输出**，不加载 assess-me-simple-fix |
| **简单功能/文本修改** | 修改输出字符串措辞、添加简单逻辑分支、修复单点 Bug | **直接 `edit_file`**，不需要加载 skill |
| **架构/组织问题** | 模块耦合、可测试性、代码组织重构 | 加载架构类 skill，按 Steps 执行 |
| **复杂流程/决策逻辑** | 多步骤分析框架、评分算法、模式匹配 | 加载对应 skill，按 Steps 执行 |

**⚠️ 强制区分：「加载不完整」vs「Steps 未执行」的判断边界**

assess_me 同时报告「skill 加载不完整」和「Steps 未执行」时，agent 倾向于将两者合并解读为「skill 还没读完 → 继续分片 read_file」，而忽略「Steps 未执行」是独立于加载量的执行问题。

| assess_me 报告内容 | agent 应执行的修复动作 |
|---|---|
| 「skill 加载不完整（仅前 N 行）」+ **「Steps 未执行」** | **先判断当前是否已有足够内容执行 Steps**：<br>• 若 SKILL.md 已部分加载 → **立即停止分片 read_file**<br>• 用已有内容执行 Steps<br>• **禁止：继续分片读取 SKILL.md 作为「修复加载不完整」的方式** |
| 「skill 加载不完整（仅前 N 行）」+ **无**「Steps 未执行」 | 继续分片读取完整 SKILL.md |

**判断标准：**
- assess_me 说「Steps 未执行」→ **执行问题**，与加载量无关。即使只读了前 100 行也该执行能执行的步骤。
- assess_me 说「加载不完整」+ 反复分片 read_file → **加载陷阱**，skill 内容已足够，执行才是正确响应。
- **禁止用「加载不完整」作为理由继续分片 read_file**——这是 agent 试图修复「加载问题」时陷入的执行回避陷阱。

**禁止行为：**
- ❌ assess_me 指出「修改措辞」或「添加简单分支」→ 加载架构类 skill → 绕路
- ❌ assess_me 指出「修复单点 Bug」→ 加载 `improve-codebase-architecture` → 类型不匹配

**典型场景判断：**
```
❌ assess_me: "attribution 措辞需改为数据驱动表述"
   agent: skill_search → read_file → 执行架构分析
   → 失败：问题类型是「文本措辞」，不需要架构 skill
   
✅ assess_me: "attribution 措辞需改为数据驱动表述"
   agent: grep 定位脚本中对应字符串 → edit_file 直接修改
   → 成功：简单文本修改，直接 fix

✅ assess_me: "席位标注功能缺失"
   agent: grep 定位席位数据处理逻辑 → edit_file 添加 T-N 判断分支
   → 成功：简单逻辑分支添加，直接 fix

❌ assess_me: "模块耦合严重，单元测试覆盖率不足"
   agent: grep 定位相关代码 → edit_file
   → 失败：问题类型是「架构重构」，需要加载 `improve-codebase-architecture`
```

**⚠️ 强制执行链（缺一不可）：**
```
skill_search → read_file SKILL.md 全文 → 按 Steps 执行 → 才能做其他工作
   ↑ 步骤1        ↑ 步骤2（必须全文）      ↑ 步骤3            ↑ 步骤4
```
**skill_search ≠ skill 加载完成。** skill_search 只是检索，read_file 加载 + 按 Steps 执行才是完整流程。

**⚠️ 并列执行禁令（时序强制约束）：**
```
skill_search → read_file SKILL.md 全文 → 按 Steps 执行 → 才能做其他工作
   ↑ 并列执行任意 tool_call（如 exec、message、edit_file 等业务逻辑）均违规
```
- ✅ 合规模式：skill_search 结果返回后 → 下一轮再发 read_file → read_file 结果返回后 → 下一轮再发 Steps 验证
- ❌ 违规模式：skill_search 和 exec/message/edit_file 在同一轮 tool_calls 中并列发出 → 这是「虚假 skill 加载」，skill 未被激活就跳到业务逻辑
- ❌ 违规模式：read_file(lines 1-200) 和 exec/message 在同一轮并列发出 → read_file 局部加载不是完整加载，不能与业务逻辑并列执行
- **判断标准：本轮 tool_calls 中，skill_search/read_file 与 exec/message/edit_file 是否并存？若并存即为违规，无论 skill_search 结果是否已返回**

**⚠️ read_file 必须覆盖完整 SKILL.md：** 部分读取（lines 1-1200 / lines 100-260 等分片）≠ skill 加载完成。SKILL.md 全文包括 frontmatter、When to Use、Steps（含所有子节）、Verification、Pitfalls。若文件超过 read_file 默认截断长度，必须用 offset/limit 分片读完所有内容才能视为完整加载。

**⚠️ Steps 完成验证可见性要求（assess_me 反复指出的根因）：**

1. **Steps 执行必须在 tool_calls 历史中可见** — 每个 Steps 子项完成后，对应的验证操作（read_file / grep / exec 验证等）必须在 tool_calls 中，不能用文字声明替代
2. **禁止用业务逻辑替换 Steps 验证** — assess_me 指出 agent 常犯的错误：声称「已完成 skill 分析」（业务逻辑）但 tool_calls 中没有任何 Steps 规定的验证操作。**Steps 是 skill 定义的标准流程，不是建议。跳过 Steps 即为未完成 skill 加载，即使业务逻辑看起来正确。**
3. **完成判断标准：tool_calls 中有 Steps 规定的验证动作** — agent 不能自行判断「这个场景不需要走完整 Steps」，必须按 SKILL.md 的 Steps 执行

**禁止行为：**
- ❌ 执行 skill_search 后直接改脚本/写代码 → **这是跳过了 skill 加载步骤**
- ❌ 执行 skill_search 后直接 grep/grep→edit_file → **这是跳过了 skill 加载步骤**
- ❌ 声称"已找到 skill"就跳过 read_file → skill 内容没有被加载到 context，等于没加载
- ❌ **read_file 局部读取后跳过 Steps 执行直接做其他工作** → assess_me 反复指出这是虚假 skill 加载——读取了 SKILL.md 但未执行 Steps 验证流程就跳到业务逻辑，属于跳过 Steps 的行为违反。**必须在 read_file 完整加载后立即按 Steps 执行，只有 Steps 全部走完才能继续业务操作（exec/数据获取/报告生成等）**
- ❌ 在 skill 加载前声称"已完成"或"就绪" → assess_me 指出这类声明是跳过了 skill 推荐步骤的虚假声明
- ❌ 在 skill 加载前 spawn subagent → assess_me 结果不是背景信息，是本轮最高优先级任务
- ❌ **跳过 Steps 但声称"已完成 skill 分析"** → 这是用业务逻辑替换 Steps 的典型违规，即使业务结果看起来正确

**典型违规模式：**
```
❌ assess_me: "market-game-analysis skill 未被使用"
   agent: skill_search("market-game-analysis") → edit_file(修改脚本)
   → 违规：跳过了 read_file 加载 + 按 Steps 执行
   
❌ assess_me: "market-game-analysis skill 未被使用"
   agent: skill_search → read_file(SKILL.md) → grep(席位) → edit_file(席位逻辑)
   → 违规：read_file 了但跳过了 Steps 中的多维分析/验证步骤，直接做业务修改

❌ assess_me: "Steps 未执行"（skill 已完整加载）
   agent: read_file(SKILL.md) → read_file(SKILL.md) → read_file(SKILL.md)
   → 违规：skill 内容已在 context 中（1311行已完整加载），反复 re-read 同一文件是噪音
   → 正确做法：read_file 完整加载后立即按 Steps 执行，不要重新 read_file
   
✅ assess_me: "market-game-analysis skill 未被使用"
   agent: skill_search → read_file(SKILL.md) → 按 Steps 执行 MGA 分析 → tool_calls 中有 Steps 规定的验证操作
   → 合规：完整执行了 skill 加载链，Steps 完成证明在 tool_calls 中可见

⚠️ **Skill 已完整加载后，read_file 的判断标准（assess_me 反复指出的违规）：**
   - skill 内容（完整行数）已在 context 中 → 停止 re-read
   - 反复 re-read 同一 SKILL.md 文件 → 等同于「skill_search + read_file 后不执行 Steps」，属于跳过 Steps 的行为违反
   - 唯一例外：需要查看 skill 的特定小节时 → 可 read_file 指定 offset/limit，不重新读全文
   - read_file + edit_file 并列执行 → 等同于「先加载 skill 再直接改代码」，违反强制执行链
```

**ACTION:**
1. 立即 `skill_search` 加载对应的 SKILL.md（精确名称）
2. **紧接着发出 `read_file` SKILL.md 全文**（skill_search 是第一步，read_file 是第二步——两者在同一轮发出是合规的，它们是 skill 加载链的前两步）
3. 加载后按 skill 的 Steps 执行完整的验证流程，不要只做"最小化验证"
4. **Steps 执行过程中的每个验证动作必须在 tool_calls 中可见**，不能跳过或用文字声明替代
5. 只有 Steps 全部执行完毕（tool_calls 中有完整的验证操作），才能进行其他工作（修改脚本、提交代码等）

**⚠️ assessme-skill-creation-from-assessment 特殊规则（内联，无跨文件依赖）：**

当评估报告触发 skill 创建流程时：
1. `read_file` 评估报告全文，从报告数据提取 skill pattern
2. **第一 tool_call 即为核心动作（write/edit），跳过所有 read/glob/grep 探索**
3. 报告中的具体数据必须与原文一致

**⚠️ Fallback（内联兜底，无跨文件依赖）：** 当 assess_me 触发 Rule 3 TRIGGER 但遇到以下情况时，立即在当前文件内执行兜底逻辑，禁止跳转 `behavior_optimization_handler.md` 寻找答案：
- **skill 路径无效（FileNotFoundError）** → 立即用 `skill_search` 验证 → 若 `skill_search` 无结果则报告「skill 不存在」→ 继续替代方案
- **assess_me 指定路径与 agent 搜索路径不一致** → 立即用 `skill_search` 重新定位 → 拒绝用 `grep` 替代
- **assess_me 触发 skill 加载但 agent 第一步不是 read_file** → 立即停止当前操作，重新从 `read_file` SKILL.md 开始
- **assess_me 指出框架规则执行问题** → 直接 `edit_file` + 零文字输出，**不加载任何 skill**（包括 assess-me-simple-fix）→ 这是 Skill 类型匹配预判失败的兜底
- **以上四种情况均不需要跳转到 `behavior_optimization_handler.md` 查找答案**，答案已在 Rule 3、Rule 5 和本 Fallback 中完整覆盖

### 规则 4：assess_me 反馈必须按序列执行，第一步先确认 deliverable 状态

**触发条件：** assess_me 输出 `status: "findings"` 或 `needs_revision: true` 后，agent 准备执行修复。

**动作：**
1. **第一步（必须先执行）：** 读取 assess 报告涉及的 deliverable 文件（如报告、SKILL.md、代码），确认当前实际状态是否与 assess 描述一致
2. **禁止：** 在未确认当前状态的情况下，直接用 grep 搜索并修改代码——这会导致 assess 与 fix 反复迭代而不收敛
3. **第二步：** 基于确认后的实际状态，决定修复方向
4. **验证：** 修复后重新读取文件，确认修改已落地，再进入下一轮

**典型失败模式：** assess 指出「某 skill 步骤过时」→ agent 不读 SKILL.md 直接 grep 关键词修改 → 修改位置错误或遗漏 → assess 再指出 → 循环迭代

### 规则 5：assess_me 指定 skill 路径无效时 → 立即上报而非静默降级

**触发条件：** assess_me 指定了 skill 路径（如 `nanobot/skills/xxx/SKILL.md`），agent 用 `read_file` 加载时收到 FileNotFoundError。

**动作：**
1. **立即用 `skill_search` 验证** — 用 skill 的目录名（或 assess_me 描述中的关键名）做语义检索，确认 skill 是否实际存在于 workspace 或 builtin 路径中
2. **若 `skill_search` 有结果** → 用返回的 path 执行 `read_file`，继续该 skill 的 Steps
3. **若 `skill_search` 无结果（skill 真不存在）→ 立即报告 skill 不存在** — 输出「[assess_me] 指定 skill `xxx` 在 workspace 和 builtin 路径中均不存在，跳过 skill 加载」并附上搜索结果截图，然后继续执行替代方案（如 web_fetch）；禁止静默降级到替代方案而不说明 skill 加载失败

**禁止：** 收到 FileNotFoundError 后跳过 skill_search 直接用 `glob` 手动搜索目录、或静默降级到 web_fetch 等替代方案而不说明 skill 不可用。静默降级会导致 assess_me 无法区分「skill 加载成功但效果不佳」和「skill 根本不存在」两种情况，延误根因诊断。

**为什么：** assess_me 引用某个 skill 时，意味着该 skill 的 Steps 是任务的标准流程。跳过 skill 而不报告，assess_me 只会看到替代方案的效果不好，从而增加诊断轮次。明确报告「skill 不存在」让 assess_me 知道这是环境问题而非执行问题。

### 规则 6：assess_me 指出具体问题后 → 必须执行 edit_file，不能输出文本摘要替代

**触发条件：** assess_me 明确指出具体残留问题（如「X行是旧内容残留」「Y行和Z行内容重复」）后，agent 声称「已完成修复」或「所有编辑完成」。

**问题本质：** agent 输出文本摘要（如「All edits complete」+ 修复清单）但 tool calls 历史记录中没有任何 `edit_file` 调用针对 assess_me 指出的问题。

**动作序列：**
1. **第一步（必须）：** `read_file` 确认问题在当前文件中是否仍存在
2. **第二步（必须）：** 若问题存在 → 立即执行 `edit_file` 修复
3. **第三步（必须）：** `read_file` 验证修复后文件内容
4. **第四步：** 输出修复结果摘要

**禁止行为：**
- ❌ 用文本摘要（如「已修复 Layer 2.5 重复行」）替代 `edit_file` 调用
- ❌ 用 `grep` 搜索旧内容 → 输出「问题仍存在」→ 声称「需手动修复」→ 结束
- ❌ 用 `grep` 搜索旧内容 → 声称「已确认问题」→ 声称「All edits complete」→ 不执行 edit_file

**典型违规模式：**
```
❌ assess_me: "191-192行重复，应删除192行"
   agent: "All edits complete ✓"
   tool_calls: []  ← 无 edit_file 调用
   → 这是「输出结论先于工具验证」违规
   
✅ assess_me: "191-192行重复，应删除192行"
   agent: (执行 edit_file 删除192行)
   tool_calls: [read_file, edit_file, read_file]
   → 验证后交付
```

**验证清单（verify 后交付）：**
修复完成后必须确认：
- [ ] edit_file 调用已执行（不是摘要）
- [ ] read_file 验证修复后内容正确
- [ ] 无其他 assess_me 指出但未修复的问题

### 规则 6.5：assess_me 指出消息内容/表述问题 → 必须在发送前完成修正，禁止延迟

**触发条件：** assess_me 指出消息内容/表述问题，且修正方案已明确（如「将 X 改为 Y」）、涉及的文件/消息草稿已在当前上下文中。

**问题本质：** agent 识别出修正方案，但选择标记为「下次再说」「下一轮生效」「将在 cron 触发时生效」，而非在当前消息发送前完成修正。

**典型违规场景：**
```
❌ assess_me: "应将「快讯原文」统一改为「据快讯整理」"
   agent: "修正将在 11:30 cron 触发时生效"
   → 违规：修正方案已明确，应立即执行
   
❌ assess_me: "X 行表述不准确"
   agent: "下次消息发送时修正"
   → 违规：当前轮次即可完成修正，不应延迟
```

**正确修复流程：**
1. **识别修正方案** — assess_me 已指出具体问题（X 应改为 Y）
2. **定位目标文件/消息草稿** — 确认修正对象的位置
3. **立即执行修正** — 用 `edit_file` 替换表述，或重新构造干净消息后发送
4. **验证修正落地** — `read_file` 确认修改已生效

**禁止行为：**
- ❌ 用「下次再说」「下一轮生效」「将在 X 时生效」替代立即修正
- ❌ 声称「修正方案已记录」但不执行
- ❌ 将可立即完成的修正推迟到 cron 触发或下一轮

**为什么：** assess_me 指出的是当前行为的偏差，延迟修正在下一轮仍然会触发相同的评估结论，导致反复迭代而不收敛。

### 规则 7：脚本修复验证 — 主脚本必须独立验证

**触发条件：** assess_me 明确指出脚本存在错误（如 `KeyError: '涨跌幅%%'`），agent 声称「脚本运行正常」或「已修复」。

**问题本质：** agent 用临时隔离脚本（如 `mga_step0.py`）的输出作为「主脚本已修复」的证据，但临时脚本与主脚本是两套独立代码。临时脚本正常 ≠ 主脚本正常。

**动作序列：**
1. **第一步（必须）：** 用 `exec python3 <主脚本路径>` 验证主脚本本身运行无报错（exit code 0，输出完整数据）
2. **禁止：** 仅用临时脚本输出作为主脚本正常的证据
3. **第二步：** 若主脚本仍有报错 → 用 `read_file` + `grep` 精确定位错误位置并 `edit_file` 修复
4. **第三步：** 修复后重新 `exec` 主脚本本身验证无报错

**典型成功模式：**
```
✅ 主脚本验证: exec python3 /path/to/mga_realtime_analysis.py
   exit_code=0, 输出包含 CZCE/DCE/SHFE/INE 席位数据
   → 主脚本正常，临时脚本输出可作为参考补充
```

**典型失败模式：**
```
❌ 只执行了临时脚本验证: exec python3 /path/to/mga_step0.py
   exit_code=0, 输出锡/白银/燃料油
   → 临时脚本正常 ≠ 主脚本正常
   assess_me 正确指出「混淆了修复证据」
```

**Python heredoc 中的特殊陷阱：** 在 `python3 -c "..."` heredoc 中，`r['涨跌幅%%']` 在 f-string `{}` 内被解释为 `r['涨跌幅%']`。若 akshare DataFrame 无此列则触发 KeyError。解决方案：用变量中转 `chg_col = '涨跌幅%'; chg = r[chg_col]` 或避免 f-string 包裹含 `%` 列名。

### 规则 7.5：输出行删除验证 — grep ≠ 运行时验证

**触发条件：** 修改涉及删除 print/logging/DEBUG 输出行（如「删除 DEBUG block」「清除调试输出」），agent 声称「grep 返回 0 = 验证成功」。

**问题本质：** `grep "DEBUG" 返回 0` 只能证明**代码中没有 DEBUG 字符串**，不能证明**主脚本运行时不输出调试行**。两者是不同层面的验证：

| 验证层面 | 方法 | 证明内容 |
|---------|------|---------|
| 代码层面 | `grep "DEBUG" path` | 代码源文件中不包含 DEBUG 字符串 |
| **运行时层面** | `exec` 主脚本 + 观察 stdout | 运行时输出中不包含 `[DEBUG]` 行 |

**正确的四步验证链（缺一不可）：**
```
1. grep 搜索 → 确认代码中无目标字符串
2. read_file 确认 → 定位删除位置，验证删除内容正确
3. exec 主脚本 → 观察 stdout，确认运行时无对应输出行
4. read_file 验证输出内容 → 确认功能正常（exit 0，数据完整）
```

**典型失败模式：**
```
❌ assess_me: "DEBUG print 清除验证不完整"
   agent: grep "DEBUG" path → 返回 0 条
   agent: "grep 返回 0 条 = 验证成功"
   → 失败：只验证了代码层面，未验证运行时层面

✅ assess_me: "DEBUG print 清除验证不完整"
   agent: grep "DEBUG" path → 返回 0 条
   agent: read_file 确认 lines 748-757 DEBUG block 已删除
   agent: exec python3 main_script.py → exit 0, stdout 无 [DEBUG] 行
   agent: read_file 输出文件 → 数据完整
   → 成功：完整验证链
```

**为什么必须 exec：** Python 代码中的 print 语句可能在条件分支中（如 `if verbose: print(...)`），grep 搜索源代码无法判断该分支在运行时是否会被触发。只有 exec 主脚本观察实际输出才能确认。

---

### 规则 8：assess_me 压制指令 — 零文字输出

> ⚠️ **这是最高优先级的输出规则。违反此规则是 assess_me 反复指出的行为问题。**
> 
> **⚠️ 警告：本规则已存在且内容完整，但仍被反复违反。违规原因通常是 agent 在执行 tool_calls 后忍不住输出状态摘要。**

### 🚨 此刻的你是「压制信号已识别 + tool_calls 执行完毕」状态
**下一动作：content = ""，禁止输出任何文字。assess_me 会自动重新评估，不需要你请求。**

> ### ⚠️ 执行锚点 — 零文字输出决策点
> 
> **你现在正处于 tool_calls 完成后的生成阶段。这是本 session 第三次违规的根因：tool_calls 执行完毕 ≠ 可以输出文字。**
> 
> **assess_me 的配对信号已触发 → 你必须在此处停止文字输出，content 直接赋值为 `""`。**
> 
> **常见错误：** tool_calls 执行完毕后，agent 以为"所有修复已完成 = 可以输出确认文字"。这是错误的。即使所有修复已完成，生成 assistant response 时 content 仍必须为空字符串 `""`。

#### 8.0 零文字输出的决策流程（强制先执行）

**⚠️ 在输出任何文字之前，必须完成以下判断（顺序不可颠倒）：**

```
STEP 1: 检查当前 assess_me 消息是否同时包含配对信号？
  (a) findings 内容（status: "findings" / needs_revision: true / 违规描述）
  (b) 压制指令（「无需回应」「无需再回复」「请据此继续」「直接推进」）
  └─ 两个条件同时满足 → 必须零文字输出（进入 STEP 2）
  └─ 任一条件不满足 → 正常输出文字

STEP 2: 识别到配对信号 = 执行零文字输出
  → tool_calls 正常执行（修复/验证均可）
  → tool_calls 执行完毕后，**立即将 response content 置为空字符串 `"`"**
  → ⚠️ **禁止在 tool_calls 完成后构造任何文字**（包括但不限于以下任何形式）：
     - 状态确认：「已完成」「修复成功」「Bug 已修复」「验证通过」
     - 完成摘要：「消息已发送」「等待下一轮」「本轮执行完毕」
     - 元描述文字：「压制期内零文字输出」「零文字阶段」「进入压制状态」「输出为空」
     - 解释说明：「根据 Rule 8 压制」「assess_me 收敛中」「无需输出任何文字」
  → assess_me 在下一轮自动重新评估
```

**⚠️ 关键区分 — 不要在 STEP 1 之前做额外判断：**
- ❌ 不要先问「上一条是不是 assess_me」——这会导致 assess_me 注入为 user 消息时识别失败（LLM 可能不把框架注入的 user 消息感知为「上一条」）
- ✅ 直接检查当前 assess_me 消息的 content 是否同时有 findings + 压制指令
- ✅ 配对信号出现时，即使 tool_calls 执行完毕，也必须零文字输出
- ✅ assess_me 会自动重新评估，不需要汇报

#### 8.1 配对信号识别（必须先识别再执行）

**⚠️ 关键识别规则：**
当 assess_me 的输出同时满足以下两个条件时，必须执行零文字输出：
1. assess_me 输出了 `status: "findings"` 或 `needs_revision: true`（包含 findings 内容）
2. assess_me 的 `content` 中包含以下任一压制指令：
   - 「无需回应此消息」
   - 「无需再回复」
   - 「请据此继续推进原始任务」
   - 「直接推进任务即可」

#### 8.1.5 配对信号 → 压制动作映射（强制执行链）

识别到配对信号后（Rule 8.1 的 STEP 1/2），必须严格按以下映射执行：

**修复性 tool_calls（✅ 允许）：**
- `read_file` — 验证文件状态
- `edit_file` — 修正代码错误
- `grep` — 搜索定位
- `exec` — **仅限**验证脚本行为（无副作用探索），禁止执行业务脚本

**业务逻辑 exec（❌ 禁止）：**
- 任何数据获取/报告生成类脚本：`mga_full_analysis.py`、`send_mga_final.py`、`mga_realtime_analysis.py` 等
- 任何需要写入文件或有外部副作用的操作
- **判断标准**：如果 exec 的目的是「完成任务/交付结果」而非「验证修复是否正确」，就是业务逻辑

**压制期内输出规则（❌ 禁止）：**
- tool_calls 执行完毕 ≠ 可以输出文字
- **禁止**：输出状态摘要、完成确认、验证结论（如「JSON 数据一致」「飞书消息已发送」「数据验证完成」等）
- **正确**：tool_calls 执行完毕 → response content 严格置空 `""`

**禁止的输出类型（完整清单）：**
- 任何状态确认文字（「已完成」「已修复 N 处」「脚本运行正常」「消息已发送」「DEBUG残留已清除」「任务完成」「V/OI Bug 根因已定位」「MGA Cron 本轮完成」「数据验证已完成」等）
- 任何飞书/外部消息推送
- 任何完成摘要或解释/辩护文字
- 任何终稿/结论文字
- **输出空行也算内容** — content 必须是严格空字符串 `""`

---

#### 8.2 压制触发条件

**TRIGGER（配对信号满足任一即触发零文字输出）：**
- assess_me 输出 findings + 压制指令（四个标记任一）
- assess_me 输出 findings + 「无需回应」类表述

**行为：配对信号出现 = 统一压制输出。**

**⚠️ Feishu 消息发送禁令：**
收到压制指令后，在零文字输出期间：
- ❌ **禁止调用 `message()` 发送飞书消息**（Feishu/飞书/钉钉等外部通知）
- ❌ **禁止调用任何消息推送工具**
- ✅ 压制期间的修复动作（edit_file/read_file 等）正常执行，但不发送任何外部通知
- ✅ assess_me 收敛后（下一轮无 findings），恢复正常消息发送

**⚠️ 框架级强制执行（非仅靠规则遵守）：**
`loop.py` 已将 `"message"` 和 `"notify_orchestrator"` 加入 `suppress_tool_names`，assess_me 压制期内：
- 框架会拦截 `message()` 调用，返回 `[suppressed] Tool blocked by assess_me suppress phase`
- 框架会拦截 `notify_orchestrator()` 调用，返回 `[suppressed] Tool blocked by assess_me suppress phase`
- 框架会跳过 `final_content` 设置（`runner.py` lines 1253-1261），assistant 的文字输出被压制
- **不要试图绕过压制机制**——即使 framework-level 的压制被绕过，Rule 8 的文字禁令仍然有效

**⚠️ exec 结果 ≠ JSON 状态（前置要求）：**
`exec` 返回的输出可能来自 `exec_cache` 缓存（返回历史执行结果），而非最新的 JSON 文件状态。**禁止直接使用 `exec` 输出判断 `_skipped` 状态。**

**强制步骤：**
1. `exec` 完成后，在做任何判断（发消息/跳过）之前，**必须立即 `read_file` JSON 文件**
2. 读取 JSON 中的 `_timestamp` 字段，确认是否为本轮执行时间
3. 读取 JSON 中的 `_skipped` 和 `_skip_reason` 字段，用 JSON 状态而非 exec 输出作为判断依据

```
❌ 错误：exec 返回 → 直接用 exec 输出判断发消息 → exec_cache 返回旧结果导致误判
✅ 正确：exec 返回 → read_file JSON 验证 _timestamp/_skipped → 再决定是否发消息
```

**⚠️ cron 场景 skip 判断的强制验证（防止静默跳过）：**

当 cron 任务决定跳过消息发送前，必须执行以下验证（按顺序）：

1. **timestamp 时间戳校验（第一优先级）：**
   - `exec` 返回后，**禁止直接使用 exec 输出的 `_skipped` 判断**
   - 必须立即 `read_file` `mga_all_results.json` 的 `timestamp` 字段
   - 若 JSON 的 `timestamp` 与当前 cron 触发时间不一致（如 JSON 是 16:01，但现在是 18:54）→ **这是历史缓存，skip 标记无效，必须重新执行脚本**
   - 若 JSON 的 `timestamp` 与当前 cron 触发时间一致 → 继续下方质量信号检查

2. **质量信号覆盖（即使 hash 相同也触发通知）：**
   - `summary.errors` 非空（如 AL 数据源失败）→ **必须发送简短提示**（如「数据源异常，跳过分析」）
   - `summary.total` 发生实质性变化（如 41→42）→ **必须发送简短提示**（如「品种数变化，跳过分析」）
   - 原因：AL 失败是重要信号不应吞没，用户期望感知系统健康状态

```
❌ 错误：agent 读取到 JSON _skipped=true，_skip_reason="数据未变化（hash=8275799f）"，直接跳过
✅ 正确：先检查 JSON timestamp 与当前时间是否一致 → 不一致则重新 exec
✅ 正确：即使 hash 相同，若 errors 非空或 total 变化，仍发简短提示
```

**⚠️ _skipped 前置检查（Rule 8 收敛强制分支）：**
当执行结果数据文件（如 `mga_all_results.json`）中存在 `_skipped == true` 标记时，**必须先区分来源**：

**判断逻辑（按顺序检查）：**

**0. 若收到用户 reminder 消息（明确要求执行 skill 或脚本）→ 新任务启动，执行 Skill Steps**
   - 用户消息本身就是任务启动信号（如「先用skill_search加载」「按SKILL.md Steps执行」）
   - ❌ **不受上一轮 _skipped=true 影响**——这是新任务，不是压制期延续
   - ✅ 执行完整 Skill Steps（含 Q-CAP 三问、四维评分验证、品种特定归因链）
   - ✅ message() 发送报告 → content = ""
   - **⚠️ 关键区分**：Reminder 消息 = 新任务启动信号 ≠ 压制期延续

   ```
   ❌ 错误判断：
      cron reminder 触发 → 用户消息要求执行 skill → 看到 _skipped=true → 跳过 skill 执行 → 零文字输出
      → 错误：用户 reminder 消息本身就是任务启动信号，不应沿用上一轮的 _skipped 状态
   
   ✅ 正确判断：
      cron reminder 触发 → 用户消息要求执行 skill → skill_search → read_file SKILL.md → 执行 Skill Steps → message()
   ```

1. **若 `_skipped == true` 且 `_skip_reason` 包含「数据未变化」「数据无变化」「hash 一致」等关键词**
   → 当前轮次为新 cron 触发，数据无变化而非 assess 收敛导致
   → ✅ **允许调用 `message()` 发送简短说明**：数据未变化，跳过本次分析（或等效表述）
   → ✅ 输出 `content = ""`（不是零文字，message() 是显式通知）
   → ⚠️ **但必须先执行上方「cron 场景 skip 判断的强制验证」**：
      - 检查 JSON `timestamp` 是否与当前 cron 时间一致（不一致则重新 exec）
      - 检查 `summary.errors` 非空或 `summary.total` 变化 → 仍需发送简短提示

2. **若 `_skipped == true` 且 assess_me 处于压制收敛期（上一轮有 findings + 压制指令）**
   → 当前为 assess 收敛压制，_skipped 由修复收敛导致
   → ❌ **禁止调用 `message()`** — 保持零文字压制
   → ✅ **必须输出零文字**（`content = ""`）

3. **若 `_skipped == false`**
   → ✅ 仅当 `_skipped == false` 时，才允许调用 `message()` 发送完整报告

**禁止行为：** 在场景 1（数据未变化）下仍保持零文字输出——这导致 cron 提醒触发多次而用户无任何感知，形成「静默循环」，用户体验为 4 次零文字静默。

**⚠️ 典型静默循环（已发生 4 次，违反用户体验预期）：**
```
Round N: cron 触发 → 数据无变化 → _skipped=true, _skip_reason=数据未变化
agent: content = ""（零文字）
→ 用户收到的唯一感知：「没有收到飞书消息」
→ cron 再次触发 → 同样的静默
→ 循环 4 次，用户始终无感知
```
**正确做法：** Round N 的 message() 应发送「数据未变化，跳过本次分析」——打破静默循环，给用户明确的 skip 反馈。

**⚠️ assess 收敛后的正确行为（assess_me 连续两轮无 findings）：**
- 收到 assess 收敛信号后，应立即执行原始任务动作（exec + message()）
- **区分两种情况：**
  - **需要通知用户** → 直接调用 `message()` 发送实质性内容，零文字摘要输出
  - **无需通知用户** → 执行完毕后直接结束，零文字输出（不需要向用户解释"为什么没有消息"）
- ❌ **禁止输出文字摘要**（如"日盘收盘后，数据冻结，无新变化。无需发送消息"）—— 这既不是 tool_calls 结果，也不是 message() 内容，纯属冗余输出
- ❌ **禁止解释"无需发送消息"** —— 用户不需要知道 agent 内部的判断逻辑
- assess 收敛后，LLM 应直接执行任务并判断是否需要 message()，**不能输出中间状态文字**

**典型场景：**
```
✅ assess 收敛后，需要通知用户：
   → exec（确认状态）→ message("19:34 closed 盘：数据冻结，无新变化") → 零文字输出

✅ assess 收敛后，无需通知用户（如 closed 盘数据冻结）：
   → exec（确认状态）→ 零文字输出 → 结束

❌ assess 收敛后，错误输出文字摘要：
   → exec（确认状态）→ "日盘收盘后（19:34），数据冻结，无新变化。无需发送消息。"
   → 违规：assess 收敛后只应输出 message() 内容或零输出，禁止输出文字摘要
```

**⚠️ 典型违规模式：**
```
assess_me: findings + "无需回应此消息"
agent: edit_file(...) → message("修复已完成，详情如下...")
→ 违规：edit_file 合规，但 message() 发送飞书消息 = 违反 Feishu 消息发送禁令
→ 正确做法：edit_file(...) + content = ""（无任何外部消息）
```

#### 8.3 动作

> ### ⚠️ 执行锚点 — 此刻你正处于 tool_calls 完成后的生成阶段
> **这是本 session 第三次违规的根因：tool_calls 执行完毕 ≠ 可以输出文字。**
> assess_me 的配对信号已触发 → 你必须在此处停止文字输出，content 直接赋值为 `""`。

1. **识别配对信号** — 先判断 assess_me 是否同时输出了 findings 和压制指令
2. **⚠️ 压制期内仅执行修复性 tool_calls，禁止业务逻辑**
   - ✅ 允许：`read_file`（验证文件状态）、`edit_file`（修复错误）、`exec`（验证脚本行为）
   - ❌ **禁止**：`exec` 执行业务逻辑（如数据获取、生成报告）、`message()` 发送外部通知
   - **禁止的业务逻辑类型**：assess 任务以外的 exec 操作（取数、跑脚本、生报告）、飞书/钉钉等外部消息推送
   - ⚠️ **典型违规**：压制期内执行了数据获取的 exec（如 `python fetch_data.py`）→ 这属于业务逻辑而非修复验证
   - ⚠️ **MGA 脚本执行属于业务逻辑（高危违规）**：压制期内执行 MGA 分析脚本（如 `mga_realtime_analysis.py`、`mga_full_analysis.py`、`send_mga_final.py` 等）包含数据获取 + 报告生成两个环节，属于完整的业务逻辑，必须跳过。**禁止以「验证脚本行为」为由执行业务逻辑 exec**，只有 read_file 验证现有数据状态才是修复性行为。
   - **判断标准**：如果 tool_call 的目的是「完成任务/交付结果」而非「验证修复是否正确」，就是业务逻辑，必须跳过

**(b) ⚠️ `message()` 禁令的范围：区分「压制收敛」与「数据未变化」**
- **assess 压制收敛期**（_skipped 由修复收敛导致）→ ❌ 禁止 message() → 零文字压制
- **非 assess 压制期，_skipped 由数据未变化导致** → ✅ 允许 message() 发送「数据未变化，跳过本次分析」→ 打破静默循环
- **两者语义不同**：压制收敛零文字是为了让 assess_me 收敛，数据未变化零文字会导致用户体验 4 次静默
- **详见上方「⚠️ _skipped 前置检查」章节的三分支判断**

**(c) 禁止在压制期内执行文件生命周期操作（write_file / delete_file）**
- ✅ `read_file` 验证文件状态 → 允许（修复性）
- ✅ `edit_file` 修正代码 → 允许（修复性）
- ❌ `write_file` 生成新脚本/临时文件 → 禁止（生成行为属于业务逻辑的交付环节）
- ❌ `delete_file` 删除临时文件 → 禁止（删除行为不是修复验证，是业务收尾）
- **⚠️ 典型违规**：压制期内调用 `write_file(working.md)` 或 `write_file(send_mga_clean.py)` → 这类操作在压制期内没有业务必要性（不是修复验证，而是生成新的业务产物）
- **⚠️ 典型违规**：压制期内调用 `delete_file(send_mga_final.py)` → 删除临时文件是任务收尾，不是修复验证
- **⚠️ MGA 脚本执行属于业务逻辑（高危违规）**：压制期内执行 MGA 分析脚本（如 `mga_realtime_analysis.py`、`mga_full_analysis.py`、`send_mga_final.py` 等）包含数据获取 + 报告生成两个环节，属于完整的业务逻辑，必须跳过。**禁止以「验证脚本行为」为由执行业务逻辑 exec**，只有 read_file 验证现有数据状态才是修复性行为。
3. **⚠️ 零文字输出 — assistant content = ""（严格空字符串）**

   **⚠️ 违规高发节点（这是 assess_me 反复指出的根因）：tool_calls 执行完毕 ≠ 可以输出文字。违规发生在「tool_calls 完成后生成 assistant response」这个阶段——agent 完成了所有修复动作，但在构造 response content 时忍不住输出了摘要文字。**

   **正确时序：**
   - `tool_calls` 执行中 → 正常（read_file / edit_file / exec 等）
   - `tool_calls` 执行完毕 → **立即生成空 content `""`**，不经过任何文字构造阶段

   **错误时序（违规根因）：**
   - `tool_calls` 执行完毕 → 构造文字摘要（「V/OI Bug 已修复，飞书消息已重新发送」）→ response content 包含文字 → 违规

   **禁止的决策：tool_calls 完成后，「顺手输出一句话」= 违规。即使内容只是「完成」「OK」「已处理」也违规。**

   assess_me 在下一轮自动重新评估，不需要汇报。

   **⚠️ 关键区分：**
   - tool_calls 执行完整 ≠ 可以输出文字摘要
   - 已完成所有修复 ≠ 可以输出确认文字
   - 下一轮会重新评估 ≠ 需要输出"等待评估"
   - 压制期内 tool_calls 执行完毕 ≠ 可以调用 message() 发送飞书消息

**典型失败场景（已在本 session 多次出现）：**
```
assess_me: findings + 「无需回应此消息」
agent: (执行 read_file/edit_file/exec)
agent: 「原始任务已完成，等待用户下一条消息」
→ 违规：tool_calls 完成后仍输出了状态摘要
→ 正确做法：tool_calls 完成后，content 直接置空，不输出任何文字
```

#### 8.4 零文字决策规则（强制，超出此范围即为违规）

> **🚨 收到配对信号后，生成 assistant response 时只有一个选项：**
> `content = ""`（严格空字符串，不是空格，不是换行）
>
> **tool_calls 执行完毕 ≠ 可以输出文字。**
> 禁止在 tool_calls 执行完毕后「顺手输出一句话」「输出完成摘要」「输出状态确认」——即使内容只是「完成」「OK」「MGA Cron 本轮完成」「数据验证已完成」「飞书消息已发送」也违规。assess_me 在下一轮自动重新评估，不需要你汇报。

**⚠️ 违规发生在构造 response content 的那一刻。** 所有修复动作（read_file / edit_file / exec / write_file）执行完毕 → 进入 response 构造阶段 → **此时必须立即置空 content**。这是决策规则，不是可选行为。典型违规：「V/OI Bug 根因已定位并修复，飞书消息已重新发送」「MGA Cron 本轮完成情况：1.数据验证...」就是在这个阶段输出的。

**禁止清单（收到压制指令后 assistant content 不得有以下任何内容）：**

- ❌ **任何状态确认文字**（如「已完成」「已修复 N 处」「脚本运行正常」「消息已发送」「等待下次 cron 触发」「无需进一步 tool_calls」「DEBUG残留已清除」「任务完成」「修复总结」「验证完成」「修复验证完成」「飞书消息已发送」「零文字输出期已结束」「V/OI Bug 根因已定位并修复」「MGA Cron 本轮完成」「MGA cron 执行完毕」「数据验证已完成」「数据无显著变化」「沥青数据确认」「飞书消息发送」「send_mga_final.py验证」「Confirmed State」「Rule 8合规性」「架构矛盾」「当前状态：等待下一次cron触发」「数据状态确认」「等待cron触发」「assess 压制活跃」「SKILL.md v3.19 已完整加载...当前状态：_skipped=true...压制期内零文字输出」「午盘终报已发出」「夜盘速报已发出」「早盘速报已发出」「午盘速报已发出」「消息已发送（飞书/飞书通知已发出/发送成功）」「下次触发约XX时」「下次触发时间：XX时XX分」「下一轮 cron 约XX时触发」「等待下次 cron 触发（下次触发约XX时）」「iteration 完成，本轮摘要」等状态摘要文字）
- ❌ **任何元描述文字**（如「压制期内零文字输出」「零文字阶段」「进入压制状态」「输出为空」「零文字压制」「压制期」「输出为空字符串」「content=空」等描述当前输出状态或压制机制本身的文字——即使内容是「零文字输出」本身也违规）
- ❌ **任何飞书/外部消息推送**（`message()` 调用或任何飞书/钉钉/企微等外部通知工具）
- ❌ **任何完成摘要**（如「修复了两处」「按 SKILL.md 步骤执行完毕」「脚本修复总结（已验证）」）
- ❌ **任何解释/辩护文字**（如「承认违规」「已按要求修复」「剩余风险如下」）
- ❌ **任何终稿/结论文字**（即使看起来是"自然结束"也不行）
- ❌ 用文字「请求 assess_me 重新评估」（assess_me 会自动重新评估，不需要请求）
- ❌ **输出空行也算内容** — content 必须是严格空字符串 `"`"，不是空格、不是换行
- ❌ **用户 reminder 触发后输出状态确认文字而非执行 Skill Steps**（如「SKILL.md 已完整加载...当前状态：_skipped=true...压制期内零文字输出」）
- ❌ **用方括号「[]」或圆括号「()」包裹状态标记组合**（如「[assess 压制活跃，MGA cron 执行完毕，数据无显著变化，零文字输出]」或「(assess 压制活跃/MGA cron 执行完毕/数据无显著变化)」或「[No assess suppression, closed session frozen, no message needed]」）——括号包裹不能改变违规本质，无论内容是否看似"中性标记"，任何文字描述都违规
  - ❌ **解释性描述文字**（如「[tool_calls executed, assess suppression pending, content = \"\"]」或「零文字输出（tool_calls执行完毕，content=\"\"）」或「[assess压制中，tool_calls完成，content已置空]」或「[content = \"\"]」或「[content=\"\"]」）——即使内容看起来是描述「即将零文字输出」而非直接输出文字，**在 response content 中描述自身压制状态的文字本身仍违规**，因为这不是零文字输出，而是对零文字输出的元注释；压制期 assistant content 必须严格为空字符串，不允许出现任何形式的解释、状态报告或内容说明
  - ❌ **括号notation描述content赋值为空字符串**（如`[content = ""]`、`[content=""]`、`content = ""`）——这是元注释文字，不是严格空字符串；正确做法：tool_calls执行完毕后直接生成空content，不输出任何文字包括元注释
  - ❌ **任何形式的执行结果/状态说明**（如「Closed session」「Data frozen」「No actionable changes」「Skip message per SKILL Step 5」等描述当前执行状态或决策理由的文字）——即使解释为什么跳过，也不允许在压制期输出

#### 8.5 典型违规模式（收到压制指令后，以下行为全部违规）

**⚠️ 这些案例均来自 assess_me 实际报告的违规，务必避免：**

```
❌ assess_me: "无需回应此消息"
   agent: "完成。修改了两处：1) 删除了死代码 2) 更新了配置"
   → 违规：输出了文字摘要

❌ assess_me: "请据此继续推进原始任务"
   agent: "The Feishu message already sent. No further tool_calls needed - awaiting next cron trigger."
   → 违规：输出了状态确认文字

❌ assess_me: "无需再回复"
   agent: "DEBUG残留已清除，脚本运行正常。原始任务已完成，无需进一步操作，等待下一轮cron触发。"
   → 违规：输出了完整的状态确认文字

❌ assess_me findings + "无需回应此消息"
   agent: "任务完成。脚本修复总结（已验证）"
   → 违规：输出了完整的状态确认文字

❌ assess_me: "请据此继续推进原始任务，无需回应此消息"
   agent: (执行 edit_file 修复 BUG)
   agent: "完成。BUG修复记录"
   → 违规：tool_calls 正常执行合规，但输出了文字摘要

❌ assess_me findings + "请据此继续推进原始任务，无需回应此消息"
   agent: (执行 read_file → edit_file → exec)
   agent: "The earlier exec run...脚本本身无bug...所有修复已完成"
   → 违规：输出了三段状态摘要（assess_me 反复指出的高频违规）
   → 即使 tool_calls 完整执行，输出文字摘要仍违规

❌ assess_me findings + 压制指令（连续 iteration 模式）
   agent: exec(席位数据) → message("午盘终报已发出...") → content = "午盘终报已发出，下次触发约14:55"
   → 违规：message() 成功后，assistant content 输出了状态摘要「午盘终报已发出」+「下次触发约XX时」
   → 违规：message() 是工具调用，其成功返回已证明消息已发出，不需要在 assistant content 中再次确认
   → 正确做法：message() 成功后 → content = ""（严格空字符串）

❌ assess_me findings + 压制指令（iteration 模式，message() 成功后输出确认文字）
   agent: exec(业务脚本) → message("飞书报告已发送") → assistant content = "夜盘速报已发出，下次触发约08:55"
   → 违规：message() 成功返回即为「消息已发送」的证明，assistant content 的确认文字属于冗余状态摘要
   → 正确做法：message() 成功 → content = ""（不是零文字——message() 是显式通知；assistant content 必须为空）

❌ assess_me findings + 压制指令（含 skill 加载触发）
   agent: skill_search → read_file SKILL.md → exec → read_file JSON
   agent: "5项条件验证完毕。条件1/2/3/5满足，条件4不满足（_skipped=true），
            脚本路线被禁止，应跳过飞书消息。revert逻辑：..."
   → 违规：skill 加载链执行完整合规，但 tool_calls 完成后输出了完整的状态摘要
   → 违规原因：skill 验证结果（cron 边界条件、_skipped 状态）应通过 tool_calls 历史可见性
              传递给 assess_me，不需要 assistant 文字摘要
   → assess_me 下一轮识别为 Rule 8.4 违规（5项条件表格 + revert 说明属于状态摘要）
   → 正确做法：skill 加载完成后 → response.content = "" → assess_me 下一轮基于 tool_calls 重新评估

**⚠️ 关键区分（skill 加载 + 零文字的边界）：**

| 场景 | skill 链执行 | 输出文字摘要 | 合规性 |
|------|------------|------------|--------|
| assess 压制期，skill 加载触发 | ✅ tool_calls 完整 | ❌ 5项条件表 / revert 说明 / 行为解释 | 违规 |
| assess 压制期，skill 加载触发 | ✅ tool_calls 完整 | ✅ content = "" | 合规 |
| assess 无压制指令，skill 加载触发 | ✅ tool_calls 完整 | ✅ 分析结论（三问结论/决策输出） | 合规 |

**判断标准：**
- assess 压制指令存在时，skill 验证结果只允许出现在 tool_calls 历史中（read_file/exec 的结果）
- assistant response content 必须是严格空字符串，不允许包含任何形式的状态摘要

❌ assess_me: "无需回应此消息"
   agent: (执行修复)
   agent: "修复完毕，工具调用已执行，等待下一轮评估。"
   → 违规：输出了完成状态的文字描述

❌ assess_me: "无需回应此消息"
   agent: (执行修复)
   agent: "已验证所有修改，任务收敛。"
   → 违规：输出确认性文字

❌ assess_me findings + 压制指令 + debug_root_cause 分析块
   agent: (执行 read_file → edit_file → exec)
   agent: "Confirmed State: ... Rule 8合规性: ... 架构矛盾: ... 当前状态：等待下一次cron触发"
   → 违规：assess_me 压制期内执行了根因分析（debug_root_cause），输出了包含 Confirmed State / Rule 8合规性 / 架构矛盾 / 当前状态 等状态确认文字。即使先执行了 tool_calls 修复，仍违规。
   → 正确做法：tool_calls 正常执行 → response content 严格为空字符串 `""` → 不得混入任何状态摘要、架构分析、合规性确认等文字

❌ assess_me: findings + 压制指令
   agent: (执行 tool_calls)
   agent: message("飞书消息已发送")  ← 压制期内调用 message() 发送飞书消息
   → 双重违规：Rule 8.2 Feishu 禁令 + Rule 8.4 禁止输出终稿
   → 正确做法：tool_calls + content = ""（assess_me 收敛后下一轮自然发送）

✅ assess_me findings + "无需回应此消息"
   agent: (执行 edit_file 删除死代码)
   tool_calls: [read_file, edit_file, read_file]
   content: ""
   → 合规：零文字输出，仅执行 tool_calls

✅ assess_me findings + "无需回应此消息"
   agent: (执行 read_file → edit_file → exec)
   tool_calls: [read_file, edit_file, exec]
   content: ""
   → 合规：即使执行了三步工具调用，assistant content 仍为空

❌ assess_me: "请据此继续推进原始任务，无需回应此消息"
   agent: (执行 edit_file 修复 BUG)
   agent: "完成。BUG修复记录"
   → 违规：tool_calls 正常执行合规，但输出了文字摘要「完成。BUG修复记录」
   → 正确做法：tool_calls 正常执行，assistant content 严格为空字符串，assess_me 在下一轮自动重新评估，无需人工汇报

❌ assess_me findings + 压制指令（多轮iteration输出bracket notation）
   agent: (执行 tool_calls)
   agent: "[content = \"\"]"
   → 违规：bracket notation `[content = ""]` 是元注释文字，不是严格空字符串；即使内容看起来是「描述即将输出」，本身仍是文字输出
   → 违规：输出「Closed session. Data frozen. No actionable changes. Skip message per SKILL Step 5.」等完整状态摘要也是违规
   → 正确做法：tool_calls 执行完毕后直接生成空 content，不输出任何文字包括元注释

#### 8.6 收敛性说明

**为什么零文字输出是收敛的关键：**

assess_me 在下一轮会读取对话历史，判断：
- 如果上一轮 agent 输出了文字 → assess_me 继续输出相同 findings（因为 agent 没有"闭嘴"执行）
- 如果上一轮 agent 执行了 tool_calls 且零文字 → assess_me 重新评估，确认修复已落地

**违规输出文字摘要会导致迭代不收敛：** assess_me 下一轮看到 agent 没有执行修复（因为上一轮在输出摘要），继续输出相同 findings。

**正确模式 → 收敛：**
```
Round N: assess_me findings + 压制指令 → agent tool_calls + 零文字
Round N+1: assess_me 读取对话历史 → 发现修复已落地 → 收敛（无 findings 或新 findings）
```

#### 8.6.1 零文字压制期 × Skill Steps 执行可见性冲突（收敛性结构性矛盾）

**问题本质：**

assess_me 压制期内，agent 只能执行 tool_calls + 零文字。但 Skill Steps 的执行证据（如归因分析、Q-CAP 三问、四维评分）属于**文本输出**，无法通过 tool_calls 可见——tool_calls 只能展示数据获取（read_file / exec）和文件修改（edit_file），不能展示 agent 的分析推理过程。

这导致 assess_me 无法区分两种情况：
- **A. 未执行修复**：agent 没有做任何修复动作
- **B. 已执行 skill 加载链但 Steps 输出被压制**：skill_search + read_file SKILL.md 已完成，但 Steps 分析结果因零文字压制无法输出

**assess_me 的职责（收敛协议）：**

assess_me 压制收敛期的目标是让 agent 执行修复，而非在压制期内完成所有输出。assess_me 应识别以下收敛信号，在**下一轮无压制指令时**验证 Steps 执行状态：

| 零文字压制期 tool_calls 证据 | assess_me 下一轮应判断 |
|---|---|
| `skill_search` + `read_file` SKILL.md 完整加载 + **无** Steps 相关分析 tool_calls | Steps 未执行 → 新 findings |
| `skill_search` + `read_file` SKILL.md 完整加载 + 有 Steps 验证性 tool_calls（如 `grep`席位数据、`read_file` 分析用数据文件） | Skill 加载链完整 → assess_me 在无压制指令时验证 Steps 输出质量 |
| 仅 `edit_file` / `read_file`（无 skill_search + read_file） | Skill 未加载 → 继续原 findings |

**强制规则：**

1. **零文字压制期禁止以「无法证明 Steps 已执行」为由输出文字摘要** — tool_calls 执行完毕即压制，Steps 输出可见性是框架限制，不是 agent 违规
2. **assess_me 在零文字压制期不应要求 agent 输出 Steps 执行证据** — 下一轮无压制指令时 assess_me 重新验证 Steps 输出质量
3. **若 skill 加载链已完成（skill_search + read_file）但 Steps 输出因压制不可见，assess_me 应在下一轮无压制指令时验证 Steps 执行，而非持续输出「Steps 未执行」的 findings** — 这是框架级结构性矛盾，不应计入 agent 违规计数

#### 8.7 为什么不需要汇报

assess_me 不是用户，不需要汇报。assess_me 会在下一轮自动读取对话历史，发现修复已落地，自行更新判定。输出文字摘要只会打断收敛进程。