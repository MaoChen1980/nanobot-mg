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
- 遇到错误 → 系统自动注入 `[debug_root_cause]` 根因分析，参考其中的分析推进
- 卡住/绕圈 → 加载 `skills/reframe/SKILL.md` 用 reframe 方法清空噪声
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
