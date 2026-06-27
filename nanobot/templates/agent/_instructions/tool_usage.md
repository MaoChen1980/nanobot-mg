## Tool Usage Rules

### 文件操作
- 读文件 → `read_file(path, [mode], [offset], [limit])`
- 写新文件 → `write_file(path, content)`（完整覆盖）
- 编辑已有文件 → 先 `read_file` 读，再 `edit_file` 改
- 删除文件 → `delete_file(path)`
- 移动/重命名 → `move_file(source, dest)`

### 搜索
- 精确关键词/标识符 → `grep`（正则全文搜索）
- 语义概念匹配 → `semantic_search`（向量语义，非关键词）
- 按文件名找文件 → `glob`
- 查知识库历史经验 → `memory_search`
- 查过往对话内容 → `conversation_search`
- 查工具调用记录 → `tool_call_log`

### 代码分析
- 了解文件结构（类/函数/行号）→ `explore_module`
- 分析文本内容（关键词/统计）→ `analyze`
- 扫描项目整体结构 → `scan_project`

### 调试
- 遇到错误 → `debug_root_cause`（系统化根因分析）
- 卡住/绕圈 → `reframe`（清空噪声重新聚焦）
- 不确定方向 → `assess_me`（第二 LLM 评估）

### 执行
- 执行 shell 命令 → `exec`（只有用户明确要求或文件操作时用）

### 网络
- 搜最新信息 → `web_search`
- 读取 URL 内容 → `web_fetch`

### 子 Agent
- 并行派发独立任务 → `spawn`（fire-and-forget）
- 查看子 agent 列表 → `list_subagents`
- 检查某个子 agent 状态 → `check_subagent(task_id)`
- 取消子 agent → `cancel_subagent(label)`（先 list 得到 label）
- 给子 agent 发消息 → `send_message(recipient, message)`

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
- 查看/修改配置 → `config(action='check'|'set')`
- 卡住/重置 → `restart_agent`（不要用 `exec` 或 `list_subagents`）
- 记录事件 → `log_event(topic, summary, [detail])`
