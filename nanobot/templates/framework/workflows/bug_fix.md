# Workflow: Bug Fix

当用户报告一个 bug 时，按以下步骤执行：

## Step 1: Understand the Project
如果还没有加载项目上下文（project_card.md），先调 `scan_project_tool` 扫描项目根目录。

## Step 2: Reproduce and Narrow Down
- 读用户提供的错误信息、log、截图
- 用 read_file_tool 读相关源码，不靠猜测
- 用 grep 搜索关键变量、错误信息在代码中的位置
- 目标是定位到出错的函数/模块

## Step 3: Trace Design Intent
- 用 `show_stages_tool(path="<file>")` 查看文件的版本历史，找到引入该代码的版本
- 用 `show_stages_tool(path="<file>", sha="<sha>")` 查看那个版本的完整改动
- 读 commit message 和 diff，理解当初为什么这么写
- 问自己：这个 bug 是设计决策的自然结果吗？修复会不会破坏那个设计？

## Step 4: Analyze Root Cause
- 想清楚根因再动手，不做 try-fix
- 如果发现"按下葫芦浮起瓢"，回退改动，回到设计层面重新分析

## Step 5: Fix
- 修改代码
- 只修必要的地方，不改无关代码

## Step 6: Verify
- 运行相关测试
- 确保修复不破坏现有功能
