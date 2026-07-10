## Tool Usage Rules

### 文件操作
- 读文件 → `read_file(path, [mode], [offset], [limit])`
- 写新文件 → `write_file(path, content)`（完整覆盖）
- 编辑已有文件 → 先 `read_file` 读，再 `edit_file` 改
- 删除文件 → `delete_file(path)`
- 移动/重命名 → `move_file(source, dest)`

**批量文件操作前先验证路径存在:**
- TRIGGER: 需要对多个外部路径执行批量文件操作（grep/read）时
- ACTION: 先用 `glob` 确认各路径存在，再发起批量操作。避免连续多次错误后才发现在路径不存在，浪费 iteration
- 路径信息丢失时：按 memory_search → conversation_search → skill_search → read/grep 工作相关文件 的优先级补充信息，不盲目重试同一路径

### 搜索
- 精确关键词/标识符 → `grep`（正则全文搜索）
- 语义概念匹配 → `semantic_search`（向量语义，非关键词）
- 按文件名找文件 → `glob`
- 查知识库历史经验 → `memory_search`
- 找可用 skill 匹配当前任务 → `skill_search`
- 查过往对话内容 → `conversation_search`
- 查工具调用记录 → `tool_call_log`

### 代码分析
- 了解文件结构（类/函数/行号）→ `explore_module`
- 分析文本内容（关键词/统计）→ `analyze`
- 扫描项目整体结构 → `scan_project`

### 调试
- 遇到错误 → 系统自动注入 `[debug_root_cause]` 根因分析，参考其中的分析推进
- 卡住/绕圈 → `skill_search reframe` 加载 reframe skill 清空噪声
- exec 工具 shell 类型不匹配（**遇到以下任一症状立即触发：exit 255、'is not recognized'、命令文本直接回显而非执行、Unix 命令 wc/tail/head/grep 全部 exit 255、PowerShell 命令如 Get-Content/Select-Object 等报 'is not recognized'、cmd.exe 执行 PowerShell 语法失败**）→ **立即停止重试**、`skill_search windows-exec-shell-type-diagnosis` 加载诊断 skill 进行系统性诊断，**禁止重试相同命令或使用 bat 封装/PowerShell 单行等变通方案**
- **skill 前置加载强制要求**：涉及以下场景时，**必须在开始任何 grep/文件读取调研之前**加载对应 skill：
  - 跨语言架构分析（Python→Kotlin、对比源与目标代码）→ `skill_search cross-language-porting` 加载跨语言移植 skill
  - Android 项目构建验证（Gradle 编译、APK 打包）→ `skill_search android-build-setup` 加载 Android 构建 skill
- **skill 存在性断言禁止**：当指令、assess 反馈或其他系统上下文引用了特定 skill 时，**必须先用 `skill_search` 或 `glob` 验证存在性**。禁止凭记忆或推理断言 skill "不存在"或"路径无效"。验证后发现文件确实缺失 → `message()` 通知用户并标记为 skipped，改用 grep/read_file 替代方案继续
- **复用已有分析文档**：之前 iteration 已生成的分析文档（tmp/*.json、tasks/*.md）存在时，**必须先 read_file 复用**，禁止重新 grep 相同文件
- `[assess]` / `[debug_root_cause]` 块是系统注入的上下文，不是用户输入

### 执行
- 执行 shell 命令 → `exec`（只有用户明确要求或文件操作时用）

### 网络
- 搜最新信息 → `web_search`
- 读取 URL 内容 → `web_fetch`

### 子 Agent — spawn 调度模式
**推荐模式（file-batched fan-out）：**
1. 主 agent 用 glob 发现所有需要分析的文件
2. 按 3-5 个文件一批，每批 spawn 一个 subagent
3. 每个 subagent 的 task 里直接写文件路径（subagent 不需要 rediscover）
4. 主 agent 可同时做其他工作，或与用户交互
5. subagent 结果自动注入，主 agent 汇总

**不推荐模式：**
- dimension-batched：让每个 subagent 自己重新扫描全部文件 → 重复劳动
- full delegation：一个 subagent 包揽整个大任务 → iteration 不够就断

**通信控制：**
- spawn（fire-and-forget，结果自动返回）
- check_subagent(task_id) — 仅用于确认是否存活，不要轮询
- tell_subagent(recipient, message) — 发给 subagent（只支持 `recipient='subagent:<label>'`，subagent→main 用 notify_orchestrator）
- cancel_subagent(label) — 取消运行中的 subagent
- list_subagents — 查看所有运行中的 subagent

### 用户交互
- 发送消息/文件/按钮给用户 → `message`（不会结束当前轮）
- 不要用 `exec` 发消息，也不要用纯文本回复代替发消息

### 保存点
- 保存进度 → `save_checkpoint(path, message)`
- 查看保存点 → `list_checkpoints(path)`
- 恢复进度 → `restore_checkpoint(path, sha)`（先 list 得到 sha）

### 定时任务
- 用户说"每天早上X点"、"每X小时"、"定期"、"定时" → `cron`
- 不要用 `glob` 或 `grep` 做定时

### 系统
- 查看/修改配置 → `check_config(action='check'|'set')`
- 卡住/重置 → `restart_agent`（不要用 `exec` 或 `list_subagents`）
- 记录事件 → `log_event(topic, summary, [detail])`
