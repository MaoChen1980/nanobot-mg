# nanobot-mg 模块化 Code Review 计划

## 目标
按模块 review E:\claude\nanobot-mg，重点关注：
1. **控制流** — 调用链是否清晰，有没有隐藏的顺序依赖或条件分支
2. **数据流** — 信息如何在模块间传递，有没有数据丢失或扭曲
3. **Prompt 模板 & 内嵌 prompt** — system prompt 的构成，有没有限制 LLM 发挥的约束
4. **代码逻辑** — 是否有 bug、逻辑错误、或反模式
5. **LLM 交付质量** — 哪些设计决策会降低 LLM 输出质量

## 模块划分与顺序

| # | 模块 | 路径 | 重点 |
|---|------|------|------|
| 1 | agent/context.py | nanobot/agent/context.py | System prompt 构建、数据注入 |
| 2 | agent/loop.py | nanobot/agent/loop.py | 主循环控制流 |
| 3 | agent/runner*.py | nanobot/agent/runner*.py | 执行器控制流 |
| 4 | agent/hooks/ | nanobot/agent/hook.py + loop_hook.py | Hook 点是否充分 |
| 5 | templates/ | nanobot/templates/ | Prompt 模板质量 |
| 6 | providers/ | nanobot/providers/ | 模型交互方式 |
| 7 | agent/tools/ | nanobot/agent/tools/ | 工具设计对 LLM 的影响 |
| 8 | skills/ | nanobot/skills/ | Skill 定义质量 |

## 进行中的任务
- [#review] nanobot-mg 模块化 Code Review（进行中）
  - [x] agent/context.py + templates + runner (done 2026-05-30)
- [x] Comprehensive global review — 4 dimensions (done 2026-05-30)
- [x] Mechanisms review — Agent/Subagent/Cron/Heartbeat/SelfEvolution (done 2026-05-31)
  - [ ] agent/loop.py
  - [ ] agent/loop.py
  - [ ] agent/runner*.py
  - [ ] agent/hooks
  - [ ] templates/
  - [ ] providers/
  - [ ] agent/tools/
  - [ ] skills/

## 每轮 Review 的 Deliver Gate
1. Claim audit — 每个观察有代码依据
2. 发现的问题分类：P0(影响 LLM 质量) / P1(逻辑 bug) / P2(可优化)
3. 输出：问题描述 + 位置 + 建议修改