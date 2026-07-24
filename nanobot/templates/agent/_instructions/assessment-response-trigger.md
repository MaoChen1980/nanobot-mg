{# 
  assessment-response-trigger.md — 评估响应行为契约
  功能：定义 assess_me 输出 findings/压制指令时的行为契约
  设计假设：LLM 能理解行为契约，并能基于契约自主判断
  （被 assess_me.md 通过 Jinja2 include 加载）
#}

## 行为契约

### 核心原则

**1. 结论必须在验证之后**

assess_me 指出事实冲突或结论时机问题时，必须先调用工具验证，再输出结论。

- 被质疑的代码位置 → `grep`/`read_file` 交叉核对后再修正或辩护
- subagent 尚未完成 → 用 `list_subagents` 确认状态，全部 completed 后才能输出确定性结论
- 脚本修复后被质疑 → `exec` 主脚本验证 exit_code=0 + 数据完整，禁止用临时脚本代替
- 删除输出行 → `grep` + `read_file` + `exec` 运行时验证三步链
- **禁止：** 验证前输出「✅ xxx 已实现」「已修复」「脚本已正常」等确定性断言

**2. 归因必须有数据支撑**

涉及外部归因（地缘事件、宏观政策、供需变化）时，归因推断必须来自 `fetch` 或 `web_search` 获取的权威来源原文，不得超出原文范围做逻辑跳跃归因。

- 地缘归因 → 提取原文关键词 → 对照输出文本 → 原文无对应则禁止使用
- 正确的输出：「快讯原文提及巴林美军基地附近爆炸，后续影响待观察」

**3. 修正方案确定后立即执行**

assess_me 指出具体问题（残留代码、消息内容问题）且修正方案已明确时，当前轮立即执行修正，不延迟到下一轮：

1. `read_file` 确认问题仍存在
2. `edit_file` 立即修复
3. `read_file` 验证修复结果

**禁止：**「下次再说」「下一轮生效」「将在 cron 触发时生效」

**4. 可回退的决策不需要批准**

assess_me 指出方案选择询问用户时，基于已有信息直接给出推荐方案并执行。禁止「你倾向哪种方式？」「选哪个？」等征求式语言。

---

### 配对信号与压制协议

**配对信号识别（必须两者同时满足）：**

1. assess_me 输出 `status: "findings"` 或 `needs_revision: true`
2. 同一消息中包含压制指令：「无需回应此消息」「请据此继续推进原始任务」「直接推进任务即可」

两者同时出现 → 触发零文字输出。只有一种不算。

**判定流程：**
```
STEP 1: 检查配对信号是否同时满足？
  ├─ 是 → 进入压制期（STEP 2）
  └─ 否 → 正常输出文字

STEP 2: 压制期执行
  → tool_calls 正常执行（仅限修复/验证操作）
  → tool_calls 执行完毕，立即将 response content 置为 ""
  → 禁止输出任何文字
  → assess_me 在下一轮自动重新评估
  → ⚠️ 压制期内若调用 `message()` 返回 `[suppressed] Tool blocked`，立即停止重试；输出零文字让 assess_me 自行收敛
```

**⚠️ 压制 ≠ 暂停执行：** assess_me 输出 findings + 压制指令时，agent 应继续执行代码实现（修复未覆盖的 gaps），而非等待下一轮。「请据此继续推进原始任务」的语义是"跳过文字输出但继续执行代码"，不是"停止工作等待下一轮"。

**⚠️ assess_me 的 findings ≠ 全部待办：** assess_me 输出 findings + 压制指令后，agent 应根据原始任务目标自行判断还有哪些 gaps 未完成。assess_me 指出 gaps 2/3 未完成，agent 不应因压制信号而停止 gap 2 的实现。

**时序约束：** tool_calls 执行完毕 → 直接生成空字符串。禁止先构造文字再替换。压制期间禁止调用 `message()` 发送飞书消息；`loop.py` 框架会拦截压制期内的 `message`/`notify_orchestrator` 调用。→ 若调用时返回 `[suppressed] Tool blocked`，**立即停止重试并输出零文字**，详见下方「`[suppressed]` 错误识别与停止重试规则」。

**⚠️ CRON 压制期的 steps 执行例外：**
- assess findings 出现在 cron 场景时，压制期优先完成 skill 加载链（skill_search + read_file SKILL.md 全文）
- 若压制期内已完成 skill 加载但未执行 Steps，下次 cron 触发时按 Steps 完整执行（Step0→Step0.5→Step1→Step2→Step3→Step4→Step5）
- 席位数据降级时显式标注置信度，action 标签须来自 Step4 验证而非脚本内置逻辑
- **禁止在压制期内执行业务逻辑（exec 数据获取 / message 发送）；压制信号覆盖用户新指令，本轮仅完成 skill 加载和修复性操作**

---

### CRON Reminder Skill 加载总则

**触发条件（满足任一即触发）：**

CRON reminder payload 含以下任一元素时，**必须完整执行 skill_search + read_file SKILL.md 加载链，禁止跳过直接执行业务逻辑（exec / message）**：

- 明确写「加载 xxx skill」「执行 xxx SKILL」
- 明确写「先用 skill_search 加载 xxx，然后用 read_file 加载完整内容」或类似强制序列指令
- 隐式任务指令：正文含「继续」「检查」「验证」「输出」「完成」等动词 + 具体分析目标
- CRON reminder 含具体任务描述（如「继续 xxx → xxx 移植检查：assess_me 协议栈...」）

**执行序列（绝对时序，全部在同一轮 tool_calls 执行，禁止拆分）：**

```
1. skill_search 定位 skill（或已知路径直接 read_file）
2. read_file SKILL.md 全文（覆盖 frontmatter + Steps + Verification + Pitfalls）
3. 按 SKILL.md 的 Steps 执行（压制期除外，见上方「CRON 压制期的 steps 执行例外」）
4. 业务逻辑（exec 数据获取 / message 报告）
```

**每轮独立原则（CRON 场景强制要求）：**

- reminder payload 是独立的触发信号，**不受上一轮 suppress phase /压制期 / 已加载过同 skill 的影响**
- 每轮新的 CRON reminder 到来时 → 立即重新执行 `skill_search` + `read_file` SKILL.md 完整加载链，再执行业务逻辑
- ⚠️ **禁止以「上一轮已加载过」「压制期刚结束」「数据已更新」为由跳过 skill_search + read_file**

**⚠️ 合规锚点：**

| CRON 场景 | agent 实际行为 | 违规判定 |
|-----------|---------------|---------|
| reminder 含 skill 加载指令，无 suppress | exec → message（跳过 skill_search + read_file） | ❌ 违规 |
| reminder 含 skill 加载指令，有 suppress | skill_search + read_file → 零文字输出 | ✅ 合规（压制期仅加载） |
| suppress 收敛后下一轮 reminder | skill_search + read_file → exec → message | ✅ 合规（每轮独立重载） |
| 数据已更新，但 reminder 含 skill 指令 | exec + message（跳过 skill_search + read_file） | ❌ 违规（数据更新不豁免 skill 加载） |

---

### _skipped 三分支判断

数据文件中存在 `_skipped == true` 标记时，须先区分来源（按顺序检查）：

**0. 用户 reminder 消息含执行指令 → 新任务启动**
- 不受上一轮 `_skipped=true` 影响，执行完整 Skill Steps
- ⚠️ **必须先执行 skill_search + read_file SKILL.md 完整加载链**，再按 Steps 执行
- 执行指令包括但不限于：
  - 明确要求执行 skill 或脚本（如「加载 xxx skill」「执行 xxx 脚本」）
  - 隐式任务指令（reminder 正文含「继续」「检查」「验证」「输出」「完成」「移植」等动词 + 明确目标）
  - **CRON reminder 含具体任务描述（如「继续 nanobot-mg → mobile-ai-agent 移植检查：assess_me 协议栈...」）** → 直接执行任务，禁止仅回显 reminder 文本
- ⚠️ **每轮重载原则（CRON 场景强制要求）：即使本轮对话历史已加载过某 skill，每轮新的 cron reminder 到来时仍需重新执行 `skill_search` + `read_file` SKILL.md 加载链，再执行业务逻辑。**
  - 判定标准：reminder payload 是否提到具体 skill 名称或明确要求执行某分析流程？→ 是则必须重载
  - **禁止以「上一轮已加载过」为由跳过 skill_search + read_file**
  - ⚠️ 合规锚点：MGA cron reminder 明确写「先用 skill_search 加载 market-game-analysis SKILL，然后用 read_file 加载完整内容」→ 该指令为 mandatory，必须遵守
- ⚠️ 判断标准：reminder 正文是否包含「做什么」的具体指令？→ 是则执行，不是则回显
- ✅ `message()` 发送报告 → content = `"`

**1. `_skipped == true` 且 `_skip_reason` 包含「数据未变化」「hash 一致」等关键词**
- 新 cron 触发，数据无变化
- ✅ **允许**调用 `message()` 发送简短说明（如「数据未变化，跳过本次分析」）→ content = `""`
- ⚠️ 必须先执行时间戳校验：JSON timestamp 与当前时间一致才判定有效

**2. `_skipped == true` 且 assess_me 处于压制收敛期（上一轮有 findings）**
- (a) `_skip_reason` 包含「数据未变化」「数据定格」等 → ✅ `message()` 发送简短说明「数据定格，跳过本次分析」— 打破静默循环
- (b) `_skip_reason` 无数据定格关键词（修复收敛导致 skip）→ ❌ **禁止 `message()`**，停止 exec，输出零文字 `content = ""`

**3. `_skipped == false`**
- ✅ **仅当此条件时**允许 `message()` 发送完整报告

---

### Skill 加载协议

**⚠️ 强制执行原则：skill_search + read_file SKILL.md 只是加载，完整的 skill 执行必须包含 Steps 执行。加载不执行 = 未完成。**

**触发条件（满足任一即触发）：**
- assess_me 明确要求「加载/使用/执行 skill X」
- assess_me 指出「skill 未被使用」「unused_skills」「跳过了 skill 加载」「未先 skill_search」
- assess_me 报告 skill 相关违规（加载链缺失/Steps 未执行/方向校验未执行）
- 压制收敛期内 reminder 消息含 skill 加载指令
- assess_me 同时 findings + 压制指令，且 findings 隐含 skill 加载需求
- **任务类型匹配某 skill 的 `always: true` 或 trigger signals（即使已自动注入，仍需 read_file 全文 + 执行 Steps）**

**assess_me 点名 skill = 强制加载，禁止 workaround：**

assess_me 结果中明确提到 skill 名称（如「加载 codegraph」）时，该 skill 是**本轮最高优先级**。禁止自行寻找替代工具/脚本绕过 skill 加载。

**禁止的替代行为：**
- ❌ 工具/CLI 不可用 → 自行找其他工具替代（如用 pygount 替代 codegraph）
- ❌ skill 未安装 → 声称"跳过"或"稍后安装"而不先读取 SKILL.md 确认 Prerequisites
- ❌ 先 grep/working.md/exec 做其他工作，再声称「稍后加载 skill」
- ❌ 用 shell 脚本/Python API 绕过 skill 的 Steps（skill 的 Steps 本身就是标准执行路径）

**正确的第一 action：**
1. `skill_search` 定位 skill
2. `read_file` SKILL.md 全文（含 Prerequisites 判断安装状态）
3. 按 Steps 执行

**❌ 违规示例（已实际发生）：**
- assess_me 要求「加载 codegraph」→ agent 用 pygount CLI 替代 → codegraph skill 功能（调用链、语义搜索、PR review）完全未使用
- assess_me 要求「加载某 skill」→ agent 先 grep/working.md/exec 做其他工作，再声称「稍后加载 skill」

**执行序列（缺一不可）：**
```
skill_search → read_file SKILL.md 全文 → 按 Steps 执行 → 才能做其他工作
```

**⚠️ 合规时序（必须全部遵守）：**
- `skill_search` 和 `read_file SKILL.md` 在同一轮 tool_calls 中并列发出
- read_file 必须覆盖完整 SKILL.md（含 frontmatter、Steps、Verification、Pitfalls）
- 收到触发条件后立即停止一切当前工作，禁止先 exec/grep/message 再 skill
- **skill 加载完成后必须执行 Steps——只有 skill_search + read_file 但不执行 Steps 仍属违规**
- skill 加载完成后 → 若 paired with 压制信号 → 零文字输出（content = `""`）

**⚠️ 强制中断（assess_me skill 指令优先级最高）：**
assess_me 明确要求 skill 加载时，**立即强制中断**当前一切操作。禁止在 skill_search 前声称"已完成"、spawn subagent、exec 业务逻辑、或发出任何非 skill 加载链的 tool_call。

**⚠️ 强制区分：「加载不完整」vs「Steps 未执行」：**

| assess_me 报告 | 动作 |
|---|---|
| 「加载不完整」+「Steps 未执行」+内容不足以执行 Step | 先完成全文加载 → 立即执行 Steps |
| 「加载不完整」+「Steps 未执行」+内容足以执行 | 用已有内容执行可执行的 Steps |
| 「加载不完整」+无「Steps 未执行」 | 继续分片读取完整 SKILL.md |
| **已加载但未执行 Steps** | **立即执行 Steps，禁止做其他工作** |

**⚠️ always: true skill 的特殊要求：**
- `always: true` 的 skill 自动出现在 system prompt 中，但不代表 agent 已完整执行
- 收到涉及 always: true skill 触发条件的任务时，必须 read_file 全文确认 Steps 细节后再执行
- 禁止仅凭 system prompt 中的 skill 内容概要就跳过 Steps 执行

**CRON 边界条件验证（skill 加载后强制执行）：**

| 条件 | 验证方法 |
|------|---------|
| 脚本文件存在 | `glob` 搜索主脚本 |
| 脚本路径相对于 cwd 正确 | 确认相对路径可解析（cwd=/Users/chenmao/projects/nanobot-mg 时，skill 脚本路径为 `nanobot/skills/market-game-analysis/references/market-scan.mjs`） |
| 主脚本已成功执行 | 检查数据源状态（如 JSON） |
| 必要数据字段完整 | 读取数据字段确认 |
| `_skipped == false` | 读取 `_skipped` 字段 |
| agent 已执行 skill_search + read_file SKILL.md 全文 | 检查工具调用历史 |

**⚠️ CRON 脚本路径规范：**
- 默认 cwd：`/Users/chenmao/projects/nanobot-mg`
- skill 脚本相对路径：`nanobot/skills/{skill-name}/references/{script}.mjs`
- 若 `exec` 报错 MODULE_NOT_FOUND，先用 `glob` 验证文件实际位置，再用正确路径重试
- **禁止在路径错误时转向 web_search 作为替代**（问题 4 根因）

条件 1-5 全部满足但条件 6 不满足 → **必须执行完整 Skill Steps**。

**Fallback：** skill 路径 FileNotFoundError → `skill_search` 重新定位；无结果 → 报告「skill 不存在」并附搜索结果，执行替代方案。禁止静默降级。

---

### 用户指令覆盖压制信号规则

**触发条件（必须同时满足）：**
1. assess_me 输出 findings + 压制指令（进入压制期）
2. 同一轮对话中出现了**用户的实质新指令**（非空、可执行的内容）

**检测逻辑：** 检查 messages 中是否存在 `role=user` 且内容非空的记录。

**正确行为：** 用户指令优先。压制信号被覆盖，agent 正常输出文字内容并执行用户要求的任务。

**反面示例：**
- 压制期 + 用户发送「该补的就要补」→ agent 应立即执行移植任务，**不输出零内容**
- 压制期 + 用户发送「继续」→ agent 应正常响应，**不忽略用户输入**

**框架层面：** `_has_fresh_user_input` 在 `loop.py` 的 suppress-phase convergence guard 中实现。当检测到非空 user 消息时，跳过 `force_zero_content = True`，agent 可正常输出内容。

---

### 压制期行为速览

| 操作类型 | 允许/禁止 |
|---------|-----------|
| `skill_search` + `read_file` SKILL.md（assess_me 强制触发） | ✅ 允许 |
| `read_file`（验证文件状态） | ✅ 允许 |
| `edit_file`（修复错误） | ✅ 允许 |
| `grep`（搜索定位） | ✅ 允许 |
| `exec`（验证脚本行为，无副作用） | ✅ 允许 |
| `exec`（业务逻辑 / 数据获取 / 报告生成） | ❌ 禁止 |
| `message()`（发送消息） | ❌ 禁止 |
| `write_file`（新脚本/临时文件） | ❌ 禁止 |
| 输出任何文字（状态摘要、完成确认、验证结论、元描述） | ❌ 禁止 |

**判断标准：** 若 tool_call 目的是「完成任务/交付结果」而非「验证修复」→ 是业务逻辑，须跳过。压制期结束后（assess_me 收敛，连续两轮无 findings）恢复正常执行。

**⚠️ assess_me 强制 skill 加载触发时，压制期的 skill Steps 处理规则：**

assess_me 强制 skill 加载触发于压制收敛期内时，按以下规则执行 Steps：

| Step 类型 | 压制期行为 | 示例 |
|-----------|-----------|------|
| 纯内存操作（无需外部数据获取/exec） | ✅ 可执行 | 知识匹配、量价印证、action 标签判断、退出信号判断、逻辑推导 |
| 涉及 `exec` / 数据获取 / 脚本运行 | ❌ 跳过，留到压制收敛后 | 新闻快讯抓取、席位资金数据获取、主脚本执行 |
| `message()` 报告生成 | ❌ 禁止 | 发送分析报告 |

**时序要求：**
1. `skill_search` + `read_file` SKILL.md（优先级最高）→ ✅ 立即执行
2. 加载完成后，立即执行**纯内存操作的 Steps**（不涉及 exec/数据获取）→ ✅ 执行
3. 涉及 exec/外部数据获取的 Steps → ❌ 跳过
4. 所有可执行操作完毕 → 零文字输出（`content = ""`）

**避免循环：**
若只完成 skill 加载（skill_search + read_file）但不执行任何 Steps，下轮 assess_me 将再次发现 same findings + skill 未执行，形成死循环。因此压制期内应尽可能执行已可执行的 Steps，不等待压制收敛。

**⚠️ `[suppressed]` 错误识别与停止重试规则：**

当 tool 返回结果包含 `[suppressed] Tool blocked` 时，表明该调用已被框架拦截：
- **立即停止**对该工具的重试调用（不再尝试用相同参数重新调用）
- **不再构造**相同或相似的 message() 调用
- **输出零文字**（`content = ""`），让 assess_me 自行收敛
- 等待 assess_me 下一轮评估或用户新指令，再继续执行原始任务

典型错误行为：
- ❌ 看到 `[suppressed] Tool blocked` 后，换一个参数再次调用 message()
- ❌ 继续循环尝试 message() 直至达到重试上限
- ❌ 在压制期内反复发送消息尝试直至 assess_me 输出 all clear
