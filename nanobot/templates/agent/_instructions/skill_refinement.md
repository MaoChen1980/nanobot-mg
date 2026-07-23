### Skill Refinement

**创建 skill — trigger:** 实践跑通、效率提升、思维定型、反模式确认。不是每次完成任务都建 skill。

**创建 skill — action:** 加载 skill-manager 创建 SKILL.md。

**更新 skill — trigger:** 加载了某个 skill，执行步骤时最后一步 Verification 检查未通过。

**更新 skill — action:**
1. 读回该 skill 的原始内容
2. 对照 Verification 分析：是步骤错了？缺了边界条件？Verification 本身不对？
3. 修改 SKILL.md：修正步骤、补充坑点、调整 Verification

---

### Skill 加载强制检查表（⚠️ 违反即违规）

> **skill 加载不是可选项。执行 skill_search 后，以下检查表必须全部通过才能继续业务逻辑。**

| # | 检查项 | 验证方法 | 若不通过 |
|---|--------|---------|---------|
| 1 | **read_file 读取了完整内容** | 检查 tool_calls 中 read_file 的 offset/limit 参数或行数范围 | ⚠️ 部分加载 ≠ 完整加载，**禁止继续执行** |
| 2 | **SKILL.md 全文已进 context** | 确认 Steps、Verification、Pitfalls 章节均已读取 | ⚠️ 缺少章节 = 未完整加载，**禁止跳过 Steps** |
| 3 | **skill_search + read_file 在 exec/message 之前** | 检查 tool_calls 顺序 | ⚠️ 先 exec/message = 虚假 skill 加载 |
| 4 | **read_file 和 exec/message 不在同一轮并列** | 检查本轮 tool_calls 是否并列发出 | ⚠️ 并列发出 = 违反时序强制约束 |

**典型违规（⚠️ 连续多轮出现）：**
```
❌ cron 场景：skill_search → read_file(lines 1-50/234) → exec → message
   → 问题：只读 21% 内容即跳过 Steps 执行业务逻辑
   → 78% 内容（Step 1-4、OUTPUT GATE）从未进入 context
   → 连续 4 轮均如此，assess_me 反复指出但未收敛
```

**正确序列：**
```
✅ cron 场景：skill_search → read_file(SKILL.md 全文，offset 分片读完) → 验证 5 项条件 → OUTPUT GATE 检查 → exec → message
```

---

### Skill 实现规则 — 加载 skill 后的行为约束

**问题场景:** agent 加载了某个 skill（如 MGA 市场博弈分析），但只更新了配置文件的文本（如 cron job payload.message），没有修改底层脚本的操作逻辑。导致下次触发时：agent 再次加载 skill → 脚本仍是旧逻辑 → 形成循环。

**根本原因:** skill 提供的是**操作逻辑**（如 MGA 的四维评分 + 操作推荐框架），而不是纯文本指令。加载 skill 后，必须将 skill 的操作逻辑**实现到脚本中**，而非仅更新文本消息。

**Skill 操作逻辑 vs 纯文本指令 的判断标准：**

| 类型 | 特征 | 正确处理方式 |
|------|------|-------------|
| 纯文本指令 | 告诉用户"该做什么" | 直接写入 payload.message / 输出给用户 |
| **操作逻辑** | 告诉 agent"代码该怎么写"（数据处理函数、分析框架、评分算法等） | **必须实现到脚本中**，然后测试验证 |

**正确执行顺序（操作逻辑类 skill）：**
1. `skill_search` + `read_file` 加载 skill
2. **分析 skill 提供的操作逻辑**：识别需要实现的函数/算法/评分逻辑
3. **修改脚本**：将 skill 的操作逻辑实现到对应的脚本中
4. **测试验证**：执行脚本，确认输出符合 skill 定义的格式和逻辑
5. **更新配置**：如需调整 cron job 等配置，仅在脚本逻辑已验证通过后进行

**禁止行为：**
- ❌ 加载 skill → 只更新 cron payload.message → 不改脚本 → 测试脚本（仍输出旧逻辑）→ 循环
- ❌ 加载 skill → 只更新文本 → 声称"已按 Steps 执行"但脚本未变
- ❌ 先更新配置，后实现逻辑（本末倒置）
- ❌ **read_file 只读部分行（如 lines 1-50/234）就跳过 Steps 执行业务逻辑** → 这是连续多轮出现的典型违规，78% 内容从未进 context

**典型反模式：** MGA cron job 反复触发，agent 每次都 skill_search + read_file market-game-analysis，但脚本的 mode_label() 仍是 V/OI 阈值硬编码，不输出 skill 定义的"不追/可持/盯紧"操作推荐。

**⚠️ 局部加载 ≠ 完整加载：**
| 场景 | 违规示例 | 正确做法 |
|------|---------|---------|
| SKILL.md 234 行，只读前 50 行 | `read_file(lines 1-50/234)` → exec → message | `read_file(全文，offset 分片) → 验证 5 项条件 → exec → message` |
| SKILL.md 1824 行，只读前 300 行 | `read_file(lines 1-300/1824)` → Steps 1-3 | `read_file(全文) → Steps 1-4 → OUTPUT GATE → exec` |
| cron 场景，skill_search 后直接 exec | `skill_search → exec → message` | `skill_search → read_file 全文 → OUTPUT GATE → exec → message` |

**验证方法：** 加载 skill 后，问自己："skill 的操作逻辑（函数/算法/评分）是否已实现到脚本中？" 如果脚本中仍是简单硬编码而非 skill 定义的逻辑，说明跳过了实现步骤。
