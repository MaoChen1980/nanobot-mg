---
name: cron-conditional-delivery
description: '定时任务的条件执行模式。在 cron 触发后，先检查完成状态文件或日志时间戳，避免重复执行已完成的任务。

  当 cron 触发后需要判断任务是否已执行过、或需要检查完成状态再决定下一步时激活。'
version: 0.1.0
category: project-management
---

# Cron Conditional Delivery — 条件执行模式

当 cron 触发后，先检查完成状态再决定是否执行完整步骤，避免重复工作。

## When to Use

- Cron 触发后需要判断当日任务是否已完成
- 需要基于状态文件或日志时间戳决定是否执行
- 重复提醒/重复检查场景（如每日自我回顾、健康打卡等）

## Steps

### 1. 确定状态文件路径模式

```
# 完成状态文件
~/.nanobot/workspace/memory/{task-name}-{date}.done

# 或基于日志时间戳
~/.nanobot/workspace/memory/{task-log}.md 中最新条目的日期
```

### 2. 检查完成状态

```
# 方案 A: 检查 .done 文件是否存在
if [ -f "~/.nanobot/workspace/memory/daily-self-review-$(date +%Y-%m-%d).done" ]; then
    echo "今日已完成"
    exit 0
fi

# 方案 B: 读取日志检查最新日期
latest_date=$(grep -m1 '^## ' ~/.nanobot/workspace/memory/self_log.md | sed 's/## //')
today=$(date +%Y-%m-%d)
if [ "$latest_date" = "$today" ]; then
    echo "今日已完成"
    exit 0
fi
```

### 3. 执行任务

若状态检查未通过，执行完整任务流程。

### 4. 更新完成状态

任务完成后，创建状态文件或追加日志条目：

```
# 创建 .done 文件
touch ~/.nanobot/workspace/memory/daily-self-review-$(date +%Y-%m-%d).done

# 或在日志中追加今日条目
echo -e "\n## $(date +%Y-%m-%d)\n\n- [完成]" >> ~/.nanobot/workspace/memory/self_log.md
```

## Verification

- 运行后确认：
  - 状态检查正确识别已完成的日期
  - 重复触发时输出「今日已完成」而非执行完整步骤
  - 首次执行后正确创建完成状态标记

## Pitfalls

- **日期格式不一致**: 使用 `date +%Y-%m-%d` 保持格式统一
- **文件路径不存在**: 首次使用前确保目录已创建
- **时区问题**: 使用本地时区 `date`，避免 UTC/本地时间混淆

**Self-optimization**: 执行后若发现判断逻辑有误，调整检查顺序或添加更多边界条件。
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
  - **Always 审查**：如果此 skill 的行为已成为 LLM 自然倾向（不注入也会正确执行），将 frontmatter 的 `always: true` 改为 `false`，反之如果发现此 skill 值得每轮注入，改为 `always: true`
