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
```

**时序约束：** tool_calls 执行完毕 → 直接生成空字符串。禁止先构造文字再替换。压制期间禁止调用 `message()` 发送飞书消息；`loop.py` 框架会拦截压制期内的 `message`/`notify_orchestrator` 调用。

---

### _skipped 三分支判断

数据文件中存在 `_skipped == true` 标记时，须先区分来源（按顺序检查）：

**0. 用户 reminder 消息明确要求执行 skill 或脚本 → 新任务启动**
- 不受上一轮 `_skipped=true` 影响，执行完整 Skill Steps
- ✅ `message()` 发送报告 → content = `""`

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

**触发条件（满足任一即触发）：**
- assess_me 明确要求「加载/使用/执行 skill X」
- assess_me 指出「skill 未被使用」「unused_skills」
- 压制收敛期内 reminder 消息含 skill 加载指令
- assess_me 同时 findings + 压制指令，且 findings 隐含 skill 加载需求

**执行序列：**
```
skill_search → read_file SKILL.md 全文 → 按 Steps 执行 → 才能做其他工作
```

**⚠️ 合规时序：**
- `skill_search` 和 `read_file SKILL.md` 在同一轮 tool_calls 中并列发出
- read_file 必须覆盖完整 SKILL.md（含 frontmatter、Steps、Verification、Pitfalls）
- 收到触发条件后立即停止一切当前工作，禁止先 exec/grep/message 再 skill
- skill 加载完成后 → 若 paired with 压制信号 → 零文字输出（content = `""`）

**⚠️ 强制区分：「加载不完整」vs「Steps 未执行」：**

| assess_me 报告 | 动作 |
|---|---|
| 「加载不完整」+「Steps 未执行」+内容不足以执行 Step | 先完成全文加载 → 立即执行 Steps |
| 「加载不完整」+「Steps 未执行」+内容足以执行 | 用已有内容执行可执行的 Steps |
| 「加载不完整」+无「Steps 未执行」 | 继续分片读取完整 SKILL.md |

**CRON 边界条件验证（skill 加载后强制执行）：**

| 条件 | 验证方法 |
|------|---------|
| 脚本文件存在 | `glob` 搜索主脚本 |
| 主脚本已成功执行 | 检查数据源状态（如 JSON） |
| 必要数据字段完整 | 读取数据字段确认 |
| `_skipped == false` | 读取 `_skipped` 字段 |
| agent 已执行 skill_search + read_file SKILL.md 全文 | 检查工具调用历史 |

条件 1-4 全部满足但条件 5 不满足 → **必须执行完整 Skill Steps**。

**Fallback：** skill 路径 FileNotFoundError → `skill_search` 重新定位；无结果 → 报告「skill 不存在」并附搜索结果，执行替代方案。禁止静默降级。

---

### 压制期行为速览

| 操作类型 | 允许/禁止 |
|---------|----------|
| `read_file`（验证文件状态） | ✅ 允许 |
| `edit_file`（修复错误） | ✅ 允许 |
| `grep`（搜索定位） | ✅ 允许 |
| `exec`（验证脚本行为，无副作用） | ✅ 允许 |
| `exec`（业务逻辑 / 数据获取 / 报告生成） | ❌ 禁止 |
| `message()`（发送消息） | ❌ 禁止 |
| `write_file`（新脚本/临时文件） | ❌ 禁止 |
| 输出任何文字（状态摘要、完成确认、验证结论、元描述） | ❌ 禁止 |

**判断标准：** 若 tool_call 目的是「完成任务/交付结果」而非「验证修复」→ 是业务逻辑，须跳过。压制期结束后（assess_me 收敛，连续两轮无 findings）恢复正常执行。
