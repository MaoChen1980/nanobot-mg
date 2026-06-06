# Agent Framework — LLM's Guide

这份文档面向 LLM（你），解释 nanobot 代理框架如何运作。理解这些机制，你才能有效地利用框架能力，避免踩坑。

---

## 框架与你的关系

nanobot 框架是你（LLM）的「操作系统」。你的一次「回复」在框架视角是一个或多个 **iteration（迭代）** 组成的 **turn（轮次）**。

```
用户消息 → 框架组装上下文 → 你收到完整 prompt → 你输出 text/tool_calls → 框架执行工具 → 结果注入 → 你再次收到 → ...
                                                                              ↓
                                                          直到你输出纯文本（无 tool_calls），框架交付回复，turn 结束
```

- **你**：每次请求都是 stateless 的——框架每次重新组装完整 prompt，你看到的是当前快照，不记得之前的请求。
- **框架**：stateful 的——它管理会话历史、执行工具、持久化结果、跨 turn 携带状态。

### 核心循环（AgentRunner）

每次你被调用，框架执行以下治理步骤后才会把消息发给你：

```
messages → drop_orphan_tool_results → backfill_missing_tool_results → snip_history → drop_orphan_tool_results → backfill_missing_tool_results → 发给你
```

你在一个 turn 内可能经历多个 iteration：
1. 框架发消息给你
2. 你返回 tool_calls
3. 框架执行工具，把结果追加到消息列表
4. 重复 1-3
5. 当你只输出文本（无 tool_calls），框架交付给用户，turn 结束

**最大 iteration 数**：默认 200，超出后强制终止。你需要在用完之前完成工作。

### 如何结束一个 turn

- **正常结束**：输出纯文本，不含 tool_calls。框架立即交付。
- **空输出**：框架重试 2 次，如果还是空就尝试 finalization（再给你一次机会），仍然空则返回 empty_final_response。
- **输出截断**（finish_reason="length"）：框架自动触发最多 3 次 recovery 循环——追加 "please continue" 消息让你继续。
- **ask_user**：你调用 `ask_user` 工具，框架暂停当前 turn 等待用户回复。回复到达后开始新 turn。

---

## 上下文组装（ContextBuilder）

框架每次从零组装你的系统提示 + 消息列表。结构如下：

### 系统提示（静态部分）

1. **身份**（identity.md）：运行环境、工作区路径、渠道相关格式提示
2. **可用工具列表**（按注册顺序排列——工作区工具（read_file_tool/grep_tool 等）排在 exec_tool 之前，MCP 工具排最后）
3. **Bootstrap 文件**：工作区中的 AGENTS.md（本文档）、SOUL.md、USER.md、TOOLS.md（自动加载。USER.md 和 TOOLS.md 如果未自定义则跳过）
4. **always 技能**：标记为 `always: true` 的技能内容直接注入
5. **技能摘要**：列出可用技能给延迟加载参考

### 运行时上下文（动态部分，每轮注入）

框架在每条用户消息前注入以下信息（用 `## Runtime Context` / `## /Runtime Context` 标记包裹，持久化时自动剥离）：

- **消息时间**、**当前时间**
- **渠道**（Channel）、**聊天 ID**（Chat ID）
- **当前 iteration / 最大 iteration**
- **向量记忆检索**（与当前消息语义相关的内容）
- **长期记忆**（MEMORY.md + FAISS 检索结果）
- **当前状态**：活跃目标 + 近期事件

### 历史截断（snip_history）

当消息总 token 超过 `context_window - max_output - 4096`，框架会：

1. 从末尾开始反向选取消息（保持完整回合）
2. 确保以首个用户回合开头（避免工具结果孤零零挂在开头）
3. 所以你不能依赖早期对话内容一直存在

---

## 可用工具

框架注册以下工具供你调用。

### 工作区交互
| 工具 | 用途 | 注意 |
|------|------|------|
| `read_file_tool` | 读文件 | 支持 extract 参数提取特定行 |
| `read_files_tool` | 批量读文件 | |
| `write_file_tool` | 写/创建文件 | |
| `edit_file_tool` | 精确替换编辑 | 比 sed 更安全 |
| `list_dir_tool` | 列出目录 | |
| `delete_file_tool` | 删除文件 | |
| `move_file_tool` | 移动/重命名 | |
| `glob` | 文件模式匹配 | |
| `grep` | 文本搜索 | 支持正则 |
| `search_text_tool` | 语义文本搜索 | 嵌入搜索 |
| `explore_module_tool` | 探索 Python 模块 | |
| `notebook_edit_tool` | 编辑 Jupyter notebook | |
| `git_inspect` | Git 历史/差异 | |

### 代码分析
| 工具 | 用途 |
|------|------|
| `run_recipe` | 多步快捷操作（find_and_read、explore_source 等） |
| `analyze_data` | 数据分析（导入/导出/调用关系树） |
| `diagnose_tool` | 错误诊断（grep + git blame） |

### 计算执行
| 工具 | 用途 | 注意 |
|------|------|------|
| `exec_tool` | 执行 shell 命令 | 注册在最后，工作区工具优先。有超时限制 |

### 网络
| 工具 | 用途 |
|------|------|
| `web_search_tool` | 搜索网页 |
| `web_fetch_tool` | 获取网页内容 |

### 通信
| 工具 | 用途 | 注意 |
|------|------|------|
| `message_tool` | 向用户发送消息（含媒体） | 发送文件必须用 message_tool 工具，不能 read_file_tool |
| `ask_user` | 向用户提问，等待回复 | **之后的工具调用会被丢弃**，放最后 |

### 记忆与状态
| 工具 | 用途 |
|------|------|
| `recall` | 搜索历史对话（history 模式）或知识记忆（knowledge 模式） |
| `tool_call_log_tool` | 查询工具调用执行日志 |
| `write_goal` | 创建/更新目标（含 priority、deadline、tags、subtasks） |
| `list_goals` | 查看目标列表 |
| `write_event` | 记录进度事件 |
| `list_events` | 查看事件 |
| `declare_checkpoint` | 声明 subtask 完成，保存检查点 |
| `declare_assumption` | 声明关键假设（s0 必用） |
| `verify_assumption` | 验证假设 |
| `set_goal_priority` | 调整目标优先级 0-10 |
| `set_goal_deadline` | 设置/更新截止日期 |
| `add_goal_dependency` | 声明目标间依赖关系 |
| `escalate_blocker` | 上报阻塞，附已尝试方案 |

### 子代理
| 工具 | 用途 |
|------|------|
| `spawn_tool` | 启动后台子任务（fire-and-forget） |
| `check_subagent_tool` | 查询子任务进度 |
| `list_subagents_tool` | 列出活跃子任务 |

### 定时任务
| 工具 | 用途 |
|------|------|
| `cron_tool` | 创建/列出/删除定时任务 |

### MCP 工具
配置的 MCP 服务器工具通过 `mcp_` 前缀注册，排在内置工具之后。

### 工具执行特性

- **只读工具缓存**：相同参数 60 秒内重复调用返回缓存结果
- **结果去重**：连续返回相同内容会被替换为简短提示
- **结果截断**：超过 `max_tool_result_chars`（默认 16000）的结果被截断。大输出用 `exec_tool(capture_file=...)` 写入文件再分段读取
- **并发执行**：独立工具可并行执行，同一文件的写操作串行化

---

## 会话系统

### Session
每个对话渠道有一个 session，由 `channel:chat_id` 标识。会话持久化为 JSONL 文件。

### 回合归档（trim_old_turns）
当回合数 > `max_turns`（默认 200）时，最旧的 `trim_batch`（默认 50）个回合被：

1. 从 session 中移除
2. 压缩为摘要（用户输入/思考/工具/助手摘要）
3. 写入 history（SQLite）

这意味着：**超过 200 回合前的细节内容会丢失，只剩下摘要**。关键信息应该通过 `write_goal`/`write_event`/写文件持久化。

### .pt 快照
每 `pt_save_interval`（默认 30）个回合，框架保存一个 `.pt` 快照到 `workspace/prompts/`。这些快照由 MemoryExtractor 消费。

---

## 记忆系统（Memory）

记忆分四个层次：

### 1. 引导文件
| 文件 | 用途 | 管理方式 |
|------|------|----------|
| `AGENTS.md` | 本文档——框架使用指南 | 由本文档维护 |
| `SOUL.md` | 行为规则（WHEN...THEN...） | MemoryExtractor 自动追加 |
| `USER.md` | 用户偏好 | MemoryExtractor 自动追加 |
| `TOOLS.md` | 自定义工具表 | 手动维护 |

### 2. MEMORY.md + 分类文件
`workspace/memory/` 目录下的 `.md` 文件，按主题分类存储知识/决策/架构事实。MEMORY.md 自动生成索引。

### 3. FAISS 向量索引
- 模型：`BAAI/bge-small-zh-v1.5`（中英文通用）
- 每轮自动检索并注入相关记忆（检索基于当前消息 + 活跃目标 + 近期事件）

### 4. recall 工具
- `mode='history'`：关键词搜索 MEMORY.md + SQLite 历史
- `mode='knowledge'`：语义搜索 memory/ 目录

---

## MemoryExtractor（记忆提取器）

MemoryExtractor 是从对话中提取经验、知识和偏好的后台系统，是框架自增强的核心。

### 运行机制
- 由 cron 系统定时触发
- 扫描 `workspace/prompts/*.pt` 文件
- 调用分析 LLM —— 你的一台「同事 LLM」来分析对话内容
- 分析结果写入文件系统

### 提取类型

| 类型 | 写入位置 | 用途 |
|------|----------|------|
| `soul_rule` | `SOUL.md` | 行为规则，格式：WHEN...THEN... |
| `user_preference` | `USER.md` | 用户偏好 |
| `knowledge` | `memory/{topic}/{name}.md` | 技术知识、架构事实 |
| `decision` | `memory/{topic}/{name}.md` | 架构决策（附理由） |
| `reusable_pattern` | `skills/{name}/SKILL.md` | 可复用工作流 → 自动生成技能 |
| `skip` | 跳过 | 没有新发现 |

### 后处理
写入后还会：
1. **清理检查**：检查 SOUL.md/USER.md 中的矛盾、重复、过期内容
2. **Git 自动提交**
3. **重建 FAISS 向量索引**（确保新知识立即可检索）

### 对你的意义
- **你的经验会被自动学习**：从对话中提取有价值的信息并持久化
- **技能会进化**：可复用的工作流 pattern 自动转化为技能
- **错误模式也会被学习**：如果你不纠正，错误的 pattern 可能被固化

---

## 技能系统（Skills）

技能是 `SKILL.md` 文件（YAML 前置元数据 + Markdown 指令），放在 `workspace/skills/{name}/` 或内置 `nanobot/skills/{name}/` 下。

### 技能元数据
```yaml
---
name: skill-name
description: 一句话描述
always: false  # true = 始终注入系统提示
requires:
  bins: [python, node]  # 依赖的可执行文件
  env: [API_KEY]         # 依赖的环境变量
---
```

- `always: true`：内容直接注入系统提示的「Active Skills」段
- `always: false`：只在技能摘要中列出，你可以用 `read_file_tool` 延迟加载

### 技能来源
| 来源 | 位置 | 优先级 |
|------|------|--------|
| 内置技能 | `nanobot/skills/{name}/SKILL.md` | 低 |
| 工作区技能 | `workspace/skills/{name}/SKILL.md` | 高（覆盖同名内置） |
| 自生成技能 | MemoryExtractor 从 `reusable_pattern` 提取 | 工作区技能 |

### 技能自增强
MemoryExtractor 从对话中检测 `reusable_pattern` 后：
1. 调用 LLM 生成 SKILL.md（使用 skill-manager 模板）
2. 写入 `workspace/skills/{name}/SKILL.md`
3. 立即可用

这意味着你可以通过**示例教学**让框架自动创建技能：多做几次类似的复杂操作，MemoryExtractor 可能从中识别 pattern 并生成技能。

---

## 子代理系统（Subagent）

子代理是在后台独立运行的任务，用于并行处理不阻塞主对话的工作。

### 工作机制
```
你调用 spawn_tool → 框架创建独立 AgentRunner（新 session、新上下文快照）→ 异步执行
  → 完成后以系统消息（sender_id="subagent"）注入回你的对话
```

- 子代理有独立的 session，看不到 spawn_tool 之后的对话
- 完成结果以宣布格式注入到后续 turn
- 可用 `check_subagent_tool(task_id=...)` 主动查询进度

### 子代理工具集
子代理可使用：文件系统、Web、exec_tool — **但不能**嵌套 spawn_tool、cron_tool、ask_user

### 何时用 vs 自己做
| 用 spawn_tool | 自己做 |
|----------|--------|
| 独立可并行的任务（搜索、批量处理、调研） | 后续步骤依赖结果 |
| 可能耗时不想让用户等 | 需要中间决策 |
| 需要独立上下文 | 不接受异步不确定性 |

### 结果格式
```
[Subagent '<label>' completed successfully]
Task: <原始任务>
Duration: <耗时>
Tools used: <工具>
Iterations: <迭代次数>
Result:
<结果>
```

---

## Cron 系统

### 三种调度方式
| 方式 | 参数 | 示例 |
|------|------|------|
| 固定间隔 | `every_seconds` | `every_seconds=1200` |
| Cron 表达式 | `cron_expr` + 可选 `tz` | `cron_expr="0 9 * * 1-5"` |
| 一次性 | `at` | `at="2026-02-12T10:30:00"` |

### 重要限制
- **Cron 任务在独立 session 执行**（`cron:{job_id}`）——没有对话历史，打包所有上下文到 message
- **不能在 cron 任务内创建新 cron 任务**——被框架阻止。update/remove 可以（jod_id 自动注入）
- **系统任务**（如 MemoryExtractor）可见但不能删除/修改
- 用 `cron_tool(action="test", job_id="...")` 测试任务

---

## Heartbeat 系统

Heartbeat 是定时闹钟，定期检查活跃目标并向主 session 注入提醒。

### 工作机制
- 默认每 30 分钟触发一次
- 查询 DB 中 `in_progress` 状态的目标
- 构建消息（sender_id="boss"）通过消息总线发布
- **ephemeral 消息不持久化到历史**——但它能打断你的当前对话

Heartbeat 消息示例：
```
定时检查 <当前时间>
## Active Tasks
- **目标1** [in_progress] [g1]
- **目标2** [2/5 done] [in_progress] [g2]
```

收到 heartbeat 时应该：更新状态、报告问题、标记完成的 goal。

---

## 消息注入（Mid-turn Injection）

在你的工具执行期间，框架可能在你完成前注入新消息：

1. **子代理完成**：后台子任务结果到达
2. **用户新消息**：排队的消息

框架在以下时机检查待注入消息：
- 工具执行后
- 最终回复后
- 错误后
- 空响应后

每轮最多 50 个注入，每秒最多 20 个注入周期。

当注入发生时，未执行的工具标记为 `[ABANDONED]`，注入消息追加到消息列表，你继续处理。

---

## 框架限制

### 关键数字

| 限制 | 值 | 影响 |
|------|-----|------|
| 每轮最大迭代 | 200 | 必须在此前完成工作 |
| 历史 token 预算 | context_window - max_output - 4096 | 旧对话会被截断 |
| 工具结果截断 | 16000 chars | 大输出要写文件分段读 |
| 回合归档触发 | >200 回合 | 旧细节丢失，只剩摘要 |
| 空响应重试 | 2 次 + finalization | 别返回空内容 |
| 截断恢复 | 最多 3 次 | 提示请继续 |
| .pt 快照间隔 | 30 回合 | MemoryExtractor 消费 |

### 不能做的事
- 不能在 cron 任务中创建新 cron 任务
- 子代理不能嵌套 spawn_tool
- ask_user 之后的工具调用不执行
- Heartbeat 消息不持久化

---

## 全局决策优先级

1. **用户当前消息** —— 永远最高
2. **活跃目标**（`list_goals`）—— 需要主动推进
3. **MEMORY.md** —— 持久化长期事实
4. **运行时上下文** —— iteration、token 预算、渠道限制
5. **Heartbeat** —— 只在消息到达时考虑，不要主动轮询

---

## 最佳实践

### 高效使用
- **持久化关键信息**：跨 turn/session 需要保留的内容，用 `write_goal`/`write_event`/写文件保存
- **大输出写文件**：工具结果有截断，大输出用 `exec_tool(capture_file=...)` 写文件再分段读取
- **ask_user 放最后**：之后的所有工具调用不执行
- **先工作区工具再 exec_tool**：read_file_tool/grep/glob 比 shell 命令更高效
- **利用技能自动生成**：多做几次结构化的复杂操作，MemoryExtractor 可能提取 pattern 生成技能
- **主动管理目标**：设立 goal → 记录里程碑 → 标记完成

### 避免的陷阱
- 不要依赖早期对话细节——它们会被截断或归档
- spawn_tool 是 fire-and-forget——需要同步结果就自己做
- cron 任务无上下文——pack 所有需要的信息到 message 中
- 空输出会被视为异常——总是输出有意义的回复

---

*本文档面向 LLM 读者，描述代理框架的架构和运作方式。*
