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

### 规则 3：assess_me 指令加载 skill 时 → 第一行代码必须是 read_file

**触发条件（满足任一即触发）：**
- assess_me 结果（`[assess]`...`[/assess]` user 消息）出现在当前 session 中
- assess_me 在 `unused_skills` 中列出了与当前任务高度相关的 skill
- assess_me 的 assessment content 中包含「先加载 skill」「请加载 skill X」「先 read_file」「第一行代码」等显式 skill 加载指令
- assess_me 明确标注「这是规则违反，不是信息不足」

**动作：**
1. **本轮第一个 tool_call 必须是 `read_file`** — 读取该 skill 的 SKILL.md 全文（仅获取内容，不执行任何探索）
2. **立即进入核心动作** — 不发出任何 read/glob/grep 探索，直接按 skill 的 `## Steps` 执行第一 tool_call
3. 「已加载」≠「已执行」— skill 全文已在 context 中，不需要再读任何文件

**典型违规模式（立即停止并执行 skill）：**
- ❌ assess_me 结果出现 → agent 解读为"背景信息"，先去 grep/glob/exec 做原来的工作 → **最高优先级违规**（assess_me 结果就是本轮任务，不是背景）
- ❌ assess_me 说「先加载 skill」→ agent 先输出 MGA 框架分析（跳过 read_file）→ 分析截断在 Q2 价位表中间，缺失 Step 3 四维评分
- ❌ assess_me 说「先加载 skill」→ agent 用 grep 搜索关键词替代 read_file → 无法验证压缩后 skill 的 Steps 是否仍有可执行指令
- ❌ 先 git push / git commit → 再 skill
- ❌ 先 grep/read_file 调研 → 再 skill
- ❌ 声称「已就绪/已理解」跳过 skill 加载

**⚠️ 关于"继续推进原始任务"的语义澄清：**
assess_me 消息末尾的"继续推进原始任务"意思是"在执行完 assess_me 指令后，用原始任务来验证修复是否有效"，**不是让你先做原始任务再来处理 assess_me**。assess_me 结果 = 新任务指令，优先级覆盖当前所有工作。

**⚠️ 与 assessme-skill-creation-from-assessment 的特殊约定：**
该 skill 的 Core Principle 明确要求「第一 tool_call 即为核心动作（write/edit），不发出 read/glob/grep 探索」。当触发该 skill 时，步骤 1 的 `read_file` 完成后，步骤 2 直接构造并执行 write/edit tool_call，跳过所有中间探索。

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
