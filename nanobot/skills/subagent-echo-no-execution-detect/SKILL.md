---
name: subagent-echo-no-execution-detect
description: 'Subagent 静默完成（phase=tools_completed 但无输出文件）检测模式。当 subagent 完成但文件未写时激活。'
category: subagent
version: 0.1.0
---
<!--ts:1783272563.0-->

# subagent-echo-no-execution-detect

## When to Use

当 subagent 完成后预期有输出文件，但文件不存在时激活。

**典型症状**：
- subagent phase=tools_completed 但无 done 文件
- spawn 时指定了 output file 路径，但 read_file 返回 FileNotFound
- subagent final response 说"已完成"但文件未落地

## Steps

1. **检查 subagent 状态**：`list_subagents` 确认是否完成
2. **检查文件是否存在**：glob/read_file 验证 output path
3. **如果文件不存在**：
   - 检查 subagent 是否真正执行了工作（不是 spawn 后立即完成）
   - 常见根因：task 描述无具体文件路径、无 exit criteria、无 output schema
4. **接管或重 spawn**：
   - 如果 subagent 有输出 → 读取并验证
   - 如果 subagent 无输出且已完成 → 自己补做或重新 spawn

## Root Causes

- **Task 描述太泛**：无具体文件路径、无 exit criteria、无 output schema（SAV 违规）
- **spawn 时 context snapshot 缺少目录树**
- **max_iterations 不足**：subagent 在写文件前耗尽迭代

## Prevention

- 大代码库 audit 先 scan_project 获取文件路径，再 spawn
- max_iterations 设为 50+
- task 必须包含：具体文件列表 + output schema + "REPORT_COMPLETE" 标记
- spawn 后立即检查文件是否存在，不要假设 subagent 完成就代表文件已写

## Verification

- subagent 完成后立即 glob/read_file 验证 output 文件存在
- 文件不存在 → 立即检查状态并接管
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
  - **Always 审查**：如果此 skill 的行为已成为 LLM 自然倾向（不注入也会正确执行），将 frontmatter 的 `always: true` 改为 `false`，反之如果发现此 skill 值得每轮注入，改为 `always: true`
