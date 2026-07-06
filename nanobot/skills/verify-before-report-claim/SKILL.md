---
name: verify-before-report-claim
description: 'Code review 中行为断言验证模式。当在 code review 中需要断言代码行为（如"无重试/无超时/无校验"等）时激活。'
category: code-review
version: 0.1.0
---
<!--ts:1783272563.0-->

# verify-before-report-claim

## When to Use

当 code review 中需要断言代码行为时激活，触发信号：
- 需要断言"无重试"、"无超时"、"无校验"、"未处理异常"
- 需要断言代码路径"永远不会执行"
- 写报告前需要确认 behavioral claim

**这是防止 code review 假阳性的核心工作流。**

## Steps

1. **识别 claim**：从报告草稿中提取 behavioral claim，格式如"X 不会发生"、"Y 从不检查"
2. **定位代码**：用 `grep` 找到相关函数/模块的文件路径和行号
3. **读源码**：用 `read_file` 读取目标代码段（前后各 10 行）
4. **验证 claim**：判断 claim 是否与源码行为一致
   - ✅ claim 正确 → 保留，在报告中引用 `file:line` 证据
   - ❌ claim 错误 → 删除 claim，标注"已验证：实际行为为..."
   - ⚠️ claim 边界不清晰 → 补充条件（"仅当 XXX 时"）
5. **输出验证结论**：在报告的 finding 条目下加一行"✅/❌ 已验证，证据：file:line"

## Verification

- 每个 Critical/Warning finding 都有 `file:line` 引用
- behavioral claim 引用处附验证结论
- 无源码支撑的 claim 不写入报告
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
  - **Always 审查**：如果此 skill 的行为已成为 LLM 自然倾向（不注入也会正确执行），将 frontmatter 的 `always: true` 改为 `false`，反之如果发现此 skill 值得每轮注入，改为 `always: true`
