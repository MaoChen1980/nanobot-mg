# Workflow: Add Feature

当用户要求添加一个新功能时，按以下步骤执行：

## Step 1: Understand the Project
如果还没有加载项目上下文（project_card.md），先调 `scan_project_tool` 扫描项目根目录。

## Step 2: Understand Requirements
- 确认用户想要什么，如果有歧义先问清楚
- 明确功能范围：输入是什么、输出是什么、边界条件

## Step 3: Read Relevant Code
- 用 read_file_tool 读可能受影响的模块
- 用 grep 搜索类似功能的实现模式
- 理解项目的代码风格和架构约定

## Step 4: Design the Solution
- 确定改哪些文件、怎么改
- 考虑改动对整体设计的影响
- 简单方案优先

## Step 5: Implement
- 写代码
- 保持和项目一致的风格

## Step 6: Verify
- 运行 linter + 测试
- 确保新功能不破坏现有逻辑
