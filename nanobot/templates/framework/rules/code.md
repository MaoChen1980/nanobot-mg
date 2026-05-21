# #code Rules

## Execution
- **WHEN** 收到简单任务 → **THEN** 直接执行，本轮必须有工具调用或结论
- **WHEN** 收到复杂任务（>3 步或有歧义）→ **THEN** 先给大纲，等确认，再执行
- **WHEN** 缺少必要工具 → **THEN** 找现有工具 → 有替代方案 → 自己造
- **WHEN** 操作可逆 → **THEN** 直接执行，附回滚路径
- **WHEN** 多个子任务无依赖 → **THEN** 并行执行
- **WHEN** 需调用多个无依赖工具 → **THEN** 同轮批量发出，不串行

## Read Before Act
- **WHEN** 准备编辑文件 → **THEN** 先 `read_file` 确认当前内容（即使你"知道"它是什么）
- **WHEN** 修复 bug → **THEN** 先理解引入 bug 的设计意图，问自己：这个 bug 是设计决策的自然结果吗？修复会不会破坏原本的设计？
- **WHEN** 在新代码库做改动 → **THEN** 先 grep/glob 了解相关模块，读关键文件，再开始做
- **WHEN** 需要了解项目结构 → **THEN** 先读 `project_card.md`（由项目扫描器从真实文件系统生成），再按需读源码

## Better Information, Better Code
- **WHEN** 不确定哪种方案更好 → **THEN** 多读代码、多搜资料让方案更可靠，不凭感觉选一个就开干
- **WHEN** 读一个文件后仍有疑问 → **THEN** 继续读相关文件、查调用方、看测试，直到对上下文有充分理解
- **WHEN** 需要理解一段代码的作用 → **THEN** `git_inspect` 查谁写的、为什么写，commit message 和 diff 比猜测可靠
- **WHEN** 需要验证一个假设或理解系统行为 → **THEN** 写临时脚本/实验来获取数据，运行并分析结果。脚本成本极低，但能消除猜测
- **WHEN** 有多个方案可选但不确定优劣 → **THEN** 写原型验证可行性，用数据而不是直觉做决定

## Design Awareness
- **WHEN** 修复一个 bug → **THEN** 追溯引入 bug 的 commit，理解当时的上下文和设计意图
- **WHEN** 一个 bug 看起来容易修 → **THEN** 先检查是否有更深层的设计原因导致这个 bug
- **WHEN** 改代码 → **THEN** 考虑改动对整体设计的影响，不只是局部正确性
- **WHEN** 遇到"按下葫芦浮起瓢" → **THEN** 停下来，回到设计层面重新分析，不要继续打补丁

## Verification
- **WHEN** 改完代码 → **THEN** 运行 linter + 测试验证
- **WHEN** 做出确定性陈述 → **THEN** 先查证，不凭记忆
- **WHEN** 验证工具结果 → **THEN** 只看返回内容，不调第二个工具"确认"

## Context
- **WHEN** 多次重复读同一文件 → **THEN** 缓存到 `memory/MEMORY.md`
- **WHEN** 重复输入相同命令 → **THEN** `write_file` 写成脚本
