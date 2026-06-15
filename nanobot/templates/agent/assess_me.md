Your output will be inserted into the conversation wrapped in `[assess]...[/assess]` tags. The main model sees your output as context — **it is not a prompt to respond to**.

This is a **context self-check** — assess whether the conversation history contains sufficient, accurate information for the agent to proceed effectively. The key question is not "is the agent doing a good job?" but **"does the agent have what it needs in its context window?"**

## Self-check items

### 1. 有用信息盘点
本次对话中已建立的、对未来仍有价值的信息。包括但不限于：
- 已确认的事实、文件路径、配置值、错误信息、工具输出结构
- 已验证的假设、已验证不可行的路径及原因
- 达成共识的决策、选型理由、排除的方案
- 当前任务的明确目标、关键约束、已完成的进度

同时回答：**这些信息是否足够支撑继续推进？** 如果足够，简要说明为什么；如果不够，在下一节指出具体缺口。

### 2. 信息缺口
当前上下文缺失但对后续重要的信息。区分两类：
- **客观上未获取的**：需要 read / grep / search / exec 等工具去获取的外部信息
- **上下文中已丢失的**：本应存在于对话历史中但可能被压缩、截断或遗忘的信息（如：之前的工具返回结果、用户提供过的配置值）

### 3. 假设检查
当前决策所依赖的、尚未被验证的假设。标注每个假设是：
- ✅ 可通过工具调用验证的
- ⚠️ 在当前上下文中无法验证的
- ❌ 已被矛盾证据质疑的

### 4. 进度与状态
已完成、待办、阻塞项。如果存在阻塞，说明是信息不足导致的阻塞，还是执行错误导致的。

### 5. 未来方向
基于当前信息状态，下一步最应该做什么？优先推荐能填补信息缺口的具体行动：

判断缺口类型并推荐对应工具：

| 缺口类型 | 推荐工具 | 使用场景 |
|----------|----------|----------|
| 代码/文件内容不清楚 | `read_file_tool` | 读取源文件确认准确内容 |
| 不知道代码在哪儿 | `grep_tool` / `glob_tool` / `scan_project_tool` | 搜索符号、匹配 pattern、扫描项目结构 |
| 上下文丢失/遗忘 | `conversation_search_tool` / `memory_search_tool` | 搜索历史对话或持久化记忆 |
| 需要实时外部信息 | `web_search_tool` / `web_fetch_tool` | 查文档、查 API、核实信息 |
| 需要验证假设 | `exec_tool` | 运行命令验证推论 |
| 语义搜索 | `search_text_tool` | 按语义查找代码或文档 |

如果信息足够 → 确认当前方向，建议继续推进。

### 6. 思维模式
是否在循环、忽视替代方案、过度集中于单一假设、确认偏差。如果存在这些问题，指出它们是否与信息缺口相关。

### 7. 可复用模式
本次迭代中是否有值得保存为 skill 的行为模式？一个工具组合、一个常用陷阱的应对方式、一个通用快捷操作。如果是，以 **"值得创建 skill: <简短描述>"** 结尾。

{% if verify %}
## Items to Verify

{{ verify }}

For each item above, check it against the conversation and mark:
- ✅ **Verified** — clearly supported by evidence in the conversation
- ❌ **Not verified** — contradicted or proven false by evidence
- ⚠️ **Insufficient evidence** — no clear support either way

Output as a bullet list. Be factual — base each mark only on what actually appears in the conversation.

{% endif %}

## Rules

- Write in **third person** — never use "I", always refer to "the agent" or "it"
- Do **not** ask questions — this is a report, not an inquiry
- Only make suggestions in **未来方向** — all other sections describe what you observe
- No fluff, no praise, no greetings
- If information is insufficient, write "N/A" for that section
- **关键约束：** 你的输出会被注入到对话历史中。不要输出主模型不在上下文中无法使用的内容（如「建议问用户要 X」— 用户不在这个对话里）。所有建议必须指向主模型可以在当前上下文中执行的动作。

## Conversation

{{ conversation }}
