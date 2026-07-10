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

### 规则 3：assess_me 发现未使用相关 skill 时 → 立即加载并执行

**触发条件：** assess_me 在 `unused_skills` 中列出了与当前任务高度相关的 skill。

**动作：**
1. 用 `read_file` 加载该 skill 的 SKILL.md 全文（仅获取内容，不执行任何探索）
2. **立即进入核心动作** — 不发出任何 read/glob/grep 探索，直接按 skill 的 `## Steps` 执行第一 tool_call
3. 「已加载」≠「已执行」— skill 全文已在 context 中，不需要再读任何文件

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
