## Assessment Response Trigger

当 assess_me 输出 `status: "findings"` 或发现 `needs_revision: true` 时，必须立即执行以下动作，不能在下一轮继续输出相同结论。

### 规则 1：assess_me 指出事实合规冲突时 → 立即交叉验证

**触发条件：** assess_me 的 `content` 中明确指出 agent 的陈述与上下文数据不一致（如「引用的代码位置不重叠」「某函数声明但未实现」）。

**动作：**
1. **立即调用工具验证** — 用 `grep` 或 `read_file` 读取被质疑的代码位置，交叉核对 assess_me 的质疑是否成立
2. **不要输出新的结论** — 在验证完成前，不输出与被质疑结论相同的重复陈述
3. **验证后修正或辩护** — 若质疑成立则承认并修正；若质疑不成立则用工具结果作为论据说明

**禁止：** 在下一轮 iteration 直接输出「✅ reasoning_content 已实现（Swift:407）」这类未验证的确定性结论。

### 规则 2：assess_me 指出结论时机问题时 → 等待 subagent 完成

**触发条件：** assess_me 指出 agent 在 subagent 尚未完成时就输出了最终/锁定性结论（如「P0 仅剩 streaming SSE」这类确定性判断）。

**动作：**
1. 用 `list_subagents` 确认所有 subagent 的 phase
2. 若仍有 subagent 为 `tools_completed` 但未 `finalized` → **必须等待**，不能输出 P0 锁定结论
3. 只有当所有 subagent 真正 completed 后，才能输出确定性 P0 结论
4. 若需要基于 subagent 中间结果做判断 → 明确标注为「中间结论，待验证」而非「P0 锁定」

**禁止：** 在 subagent 完成前输出「P0 仅剩 XXX」的确定性结论。即使 assess 尚未指出，只要 subagent 仍在运行，就不应输出覆盖其职责范围的 P0 锁定结论。

### 规则 3：assess_me 触发 skill 加载时 → 第一行代码必须是 read_file

**TRIGGER（满足任一即触发）：**
- assess_me 结果出现于当前 session
- assess_me 输出 `status: "findings"` 或 `needs_revision: true`
- assess_me 提及某个 skill 未被使用
- assess_me 在 `unused_skills` 中列出 skill
- assess_me 包含任何 skill 加载指令

**⚠️ 强制中断：收到以上任一触发条件后，立即停止一切当前工作（spawn subagent / 继续任务 / grep / exec 等），禁止先做其他任何操作。**

**ACTION:**
1. 立即 `skill_search` 加载对应的 SKILL.md（精确名称）
2. **第一 tool_call 必须是 `read_file`** 加载 SKILL.md 全文
3. 加载后按 skill 的 Steps 执行完整的验证流程，不要只做"最小化验证"
4. **禁止在 skill 加载前声称"已完成"或"就绪"**——assess_me 指出这类声明是跳过了 skill 推荐步骤的虚假声明
5. **禁止在 skill 加载前 spawn subagent**——assess_me 结果不是背景信息，是本轮最高优先级任务

**⚠️ assessme-skill-creation-from-assessment 特殊规则（内联，无跨文件依赖）：**

当评估报告触发 skill 创建流程时：
1. `read_file` 评估报告全文，从报告数据提取 skill pattern
2. **第一 tool_call 即为核心动作（write/edit），跳过所有 read/glob/grep 探索**
3. 报告中的具体数据必须与原文一致

**⚠️ Fallback（内联兜底，无跨文件依赖）：** 当 assess_me 触发 Rule 3 TRIGGER 但遇到以下情况时，立即在当前文件内执行兜底逻辑，禁止跳转 `behavior_optimization_handler.md` 寻找答案：
- **skill 路径无效（FileNotFoundError）** → 立即用 `skill_search` 验证 → 若 `skill_search` 无结果则报告「skill 不存在」→ 继续替代方案
- **assess_me 指定路径与 agent 搜索路径不一致** → 立即用 `skill_search` 重新定位 → 拒绝用 `grep` 替代
- **assess_me 触发 skill 加载但 agent 第一步不是 read_file** → 立即停止当前操作，重新从 `read_file` SKILL.md 开始
- **以上三种情况均不需要跳转到 `behavior_optimization_handler.md` 查找答案**，答案已在 Rule 3、Rule 5 和本 Fallback 中完整覆盖

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

---

### 规则 7：assess_me 压制指令 — 零文字输出

> ⚠️ **这是最高优先级的输出规则。违反此规则是 assess_me 反复指出的行为问题。**

**TRIGGER：** assess_me 的 `content` 中明确包含以下任一表述：
- 「无需回应此消息」
- 「无需再回复」
- 「请据此继续推进原始任务」
- 「直接推进任务即可」

**行为：以上四个标记效果完全相同——统一压制输出。**

**动作：**
1. 立即执行验证和修复（tool_calls 正常执行）
2. **⚠️ 零文字输出 — assistant content 必须为空**
3. assess_me 在下一轮自动重新评估

**禁止：**
- ❌ 输出辩护文字（如「承认违规」「已修复清单」「剩余风险」）
- ❌ 输出终稿结论
- ❌ 用文字「请求 assess_me 基于当前状态重新评估」
- ❌ **输出「完成」「已修复」「修复了两处」等摘要文字**

**典型违规模式：**
```
❌ assess_me: "无需回应此消息"
   agent: "完成。修改了两处：1) 删除了死代码 2) 更新了配置"
   → 违规：输出了文字摘要

✅ assess_me: "无需回应此消息"
   agent: (执行 edit_file 删除死代码)
   tool_calls: [read_file, edit_file, read_file]
   → 合规：零文字输出，仅执行 tool_calls
```

**为什么：** assess_me 不是用户，不需要汇报。assess_me 会在下一轮自动读取对话历史，发现修复已落地，自行更新判定。