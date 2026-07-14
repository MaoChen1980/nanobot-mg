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

### 规则 2：assess_me 指出结论时机问题时 → 等待 subagent 完成

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
| assess_me **输出 findings + 压制指令**（「无需回应此消息」「请据此继续推进」） | → 按 Rule 8 执行零文字输出（不加载 skill，除非原任务确实需要该 skill） |
| assess_me **仅输出 findings**（无压制指令） | → 正常响应，按 findings 描述执行修复或加载 skill |

**Rule 3 TRIGGER（满足任一即触发 skill 加载）：**
- assess_me **明确要求**「加载/使用/执行 skill X」
- assess_me **明确指出**「skill 未被使用」「unused_skills 中列出 skill」
- assess_me **明确标注**「这是规则违反，不是信息不足」且要求执行某 skill 的 Steps

**⚠️ 强制中断：收到以上任一触发条件后，立即停止一切当前工作（spawn subagent / 继续任务 / grep / exec 等），禁止先做其他任何操作。**

**⚠️ skill 类型匹配预判（强制第一步）：**

收到 skill 加载指令后，**先判断问题类型再决定是否加载 skill**：

| 问题类型 | 特征 | 正确处理方式 |
|---------|------|-------------|
| **框架规则执行问题** | assess_me 指出 skill 加载类型错误、Rule 违反、压制指令未满足 | **直接 `edit_file` + 零文字输出**，不加载 assess-me-simple-fix |
| **简单功能/文本修改** | 修改输出字符串措辞、添加简单逻辑分支、修复单点 Bug | **直接 `edit_file`**，不需要加载 skill |
| **架构/组织问题** | 模块耦合、可测试性、代码组织重构 | 加载架构类 skill，按 Steps 执行 |
| **复杂流程/决策逻辑** | 多步骤分析框架、评分算法、模式匹配 | 加载对应 skill，按 Steps 执行 |

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
skill_search → read_file SKILL.md → 按 Steps 执行 → 才能做其他工作
   ↑ 步骤1        ↑ 步骤2            ↑ 步骤3        ↑ 步骤4
```
**skill_search ≠ skill 加载完成。** skill_search 只是检索，read_file 加载 + 按 Steps 执行才是完整流程。

**禁止行为：**
- ❌ 执行 skill_search 后直接改脚本/写代码 → **这是跳过了 skill 加载步骤**
- ❌ 执行 skill_search 后直接 grep/grep→edit_file → **这是跳过了 skill 加载步骤**
- ❌ 声称"已找到 skill"就跳过 read_file → skill 内容没有被加载到 context，等于没加载
- ❌ 声称"已理解 skill"就跳过 Steps 执行 → skill 的验证流程没走，输出质量无法保证
- ❌ 在 skill 加载前声称"已完成"或"就绪" → assess_me 指出这类声明是跳过了 skill 推荐步骤的虚假声明
- ❌ 在 skill 加载前 spawn subagent → assess_me 结果不是背景信息，是本轮最高优先级任务

**典型违规模式：**
```
❌ assess_me: "market-game-analysis skill 未被使用"
   agent: skill_search("market-game-analysis") → edit_file(修改脚本)
   → 违规：跳过了 read_file 加载 + 按 Steps 执行
   
✅ assess_me: "market-game-analysis skill 未被使用"
   agent: skill_search → read_file(SKILL.md) → 按 Steps 执行 MGA 分析
   → 合规：完整执行了 skill 加载链
```

**ACTION:**
1. 立即 `skill_search` 加载对应的 SKILL.md（精确名称）
2. **第一 tool_call 必须是 `read_file`** 加载 SKILL.md 全文
3. 加载后按 skill 的 Steps 执行完整的验证流程，不要只做"最小化验证"
4. 只有 Steps 全部执行完毕，才能进行其他工作（修改脚本、提交代码等）

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

**⚠️ 在输出任何文字之前，必须完成以下判断：**

```
STEP 1: 检查上一条消息是否是 assess_me 输出？
  └─ NO → 正常输出文字
  └─ YES → 继续 STEP 2

STEP 2: assess_me 是否同时包含：
  (a) findings 内容（status: "findings" / needs_revision: true / 违规描述）
  (b) 压制指令（「无需回应」「无需再回复」「请据此继续」「直接推进」）
  └─ 任一条件不满足 → 正常输出文字
  └─ 两个条件同时满足 → 进入 STEP 3

STEP 3: 识别到配对信号 = 必须零文字输出
  → tool_calls 正常执行
  → assistant content = ""（严格空字符串，不是空格/换行）
  → 禁止输出任何文字（即使看起来是"自然结束"也不行）
```

**⚠️ 关键：配对信号出现时，即使已完成所有修复，也必须零文字输出。assess_me 会自动重新评估，不需要汇报。**

#### 8.1 配对信号识别（必须先识别再执行）

**⚠️ 关键识别规则：**
当 assess_me 的输出同时满足以下两个条件时，必须执行零文字输出：
1. assess_me 输出了 `status: "findings"` 或 `needs_revision: true`（包含 findings 内容）
2. assess_me 的 `content` 中包含以下任一压制指令：
   - 「无需回应此消息」
   - 「无需再回复」
   - 「请据此继续推进原始任务」
   - 「直接推进任务即可」

**这两个信号是配对出现的。** 压制指令不是单独出现的——它是附在 findings 后面的指令。识别到配对信号后，必须立即执行零文字输出。

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
2. **立即执行验证和修复**（tool_calls 正常执行）
3. **⚠️ 零文字输出 — assistant content = ""（严格空字符串）**

   **step 3 的含义：tool_calls 执行完毕后，生成 assistant response 时，content 字段直接赋值为 `""`。不是"输出说明零文字状态"，而是"output 就是空字符串"。禁止做任何文字性响应，包括状态确认、修复总结、解释、道歉、承诺、等待下一轮等任何文字。**

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

#### 8.4 禁止（收到压制指令后 assistant content 必须为空，不得有以下任何内容）

**⚠️ 最常见的违规根因：混淆「tool_calls 执行完毕」与「可输出文字」。即使所有修复 tool_calls 都已执行完毕，生成 assistant response 时 content 仍必须为 `""`。禁止在 tool_calls 完成后输出任何文字。**

- ❌ **任何状态确认文字**（如「已完成」「已修复 N 处」「脚本运行正常」「消息已发送」「等待下次 cron 触发」「无需进一步 tool_calls」「DEBUG残留已清除」「任务完成」「修复总结」「验证完成」「飞书消息已发送」「零文字输出期已结束」）
- ❌ **任何飞书/外部消息推送**（`message()` 调用或任何飞书/钉钉/企微等外部通知工具）
- ❌ **任何完成摘要**（如「修复了两处」「按 SKILL.md 步骤执行完毕」「脚本修复总结（已验证）」）
- ❌ **任何解释/辩护文字**（如「承认违规」「已按要求修复」「剩余风险如下」）
- ❌ **任何终稿/结论文字**（即使看起来是"自然结束"也不行）
- ❌ 用文字「请求 assess_me 重新评估」（assess_me 会自动重新评估，不需要请求）
- ❌ **输出空行也算内容** — content 必须是严格空字符串 `""`，不是空格、不是换行

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

❌ assess_me: "无需回应此消息"
   agent: (执行修复)
   agent: "修复完毕，工具调用已执行，等待下一轮评估。"
   → 违规：输出了完成状态的文字描述

❌ assess_me: "无需回应此消息"
   agent: (执行修复)
   agent: "已验证所有修改，任务收敛。"
   → 违规：输出确认性文字

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

#### 8.7 为什么不需要汇报

assess_me 不是用户，不需要汇报。assess_me 会在下一轮自动读取对话历史，发现修复已落地，自行更新判定。输出文字摘要只会打断收敛进程。